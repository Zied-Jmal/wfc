"""
action/scouting.py - Scout Drone Physical Action Engine

WGS-84 orbits, adaptive radius, real sensor coupling, Dryden wind.
State machine:
IDLE TRANSITING ORBITING GRID_SWEEP RETURNING
"""

from __future__ import annotations

import math
import os
import time
from typing import Final, Self, TypedDict

from action.gps import GPSCoord, gps_to_ned, destination_point
from action.movement import DroneMovementEngine, CRUISE_ALTITUDE_M
from action.sensors import FireSensorSuite
from action.wind import WindModel


class HotspotDict(TypedDict):
    """Single thermal hotspot record with NED position and timestamps."""
    ned: tuple[float, float]
    peak_temp_c: float
    first_seen: float
    last_seen: float


# -- Scout flight constants (configurable via env vars) ----------
ORBIT_RADIUS_BY_SEVERITY: Final[dict[str, float]] = {
    "LOW":      float(os.getenv("SCOUT_ORBIT_RADIUS_LOW", "60.0")),
    "MEDIUM":   float(os.getenv("SCOUT_ORBIT_RADIUS_MEDIUM", "80.0")),
    "HIGH":     float(os.getenv("SCOUT_ORBIT_RADIUS_HIGH", "110.0")),
    "CRITICAL": float(os.getenv("SCOUT_ORBIT_RADIUS_CRITICAL", "140.0")),
}
DEFAULT_ORBIT_RADIUS_M: Final[float] = float(os.getenv("SCOUT_DEFAULT_ORBIT_RADIUS_M", "80.0"))
ORBIT_POINTS: Final[int] = int(os.getenv("SCOUT_ORBIT_POINTS", "16"))
ORBIT_ALTITUDE_M: Final[float] = float(os.getenv("SCOUT_ORBIT_ALTITUDE_M", "40.0"))
GRID_STEP_M: Final[float] = float(os.getenv("SCOUT_GRID_STEP_M", "35.0"))
GRID_LEGS: Final[int] = int(os.getenv("SCOUT_GRID_LEGS", "5"))
TRANSIT_SPEED_MPS: Final[float] = float(os.getenv("SCOUT_TRANSIT_SPEED_MPS", "14.0"))
ORBIT_SPEED_MPS: Final[float] = float(os.getenv("SCOUT_ORBIT_SPEED_MPS", "8.0"))
GRID_SPEED_MPS: Final[float] = float(os.getenv("SCOUT_GRID_SPEED_MPS", "6.0"))
HOTSPOT_DEDUP_RADIUS_M: Final[float] = float(os.getenv("SCOUT_HOTSPOT_DEDUP_RADIUS_M", "20.0"))


class ScoutState:
    IDLE       = "IDLE"
    TRANSITING = "TRANSITING"
    ORBITING   = "ORBITING"
    GRID_SWEEP = "GRID_SWEEP"
    RETURNING  = "RETURNING"


class ScoutActionEngine:
    """
    Physical scout drone action engine.

    Integrates:
      - DroneMovementEngine  : full 3-D kinematics + GPS
      - FireSensorSuite      : physics-based thermal / smoke / range sensors
      - WindModel            : Dryden turbulence + fire plume

    Call tick(dt) from ScoutDroneNode._telemetry_loop() every 2s.
    After tick, read sensor data from .last_sensor_readings dict.
    """

    def __init__(
        self,
        drone_id: str,
        home:     GPSCoord,
        wind:     WindModel,
    ) -> None:
        """
        Initialize the scout action engine.

        Args:
            drone_id: Unique drone identifier.
            home: Home base GPS coordinate.
            wind: WindModel instance for turbulence and plume effects.
        """
        self._movement: DroneMovementEngine = DroneMovementEngine(
            drone_id=drone_id,
            home=home,
            wind=wind,
            cruise_speed_mps=TRANSIT_SPEED_MPS,
        )
        self._sensors: FireSensorSuite = FireSensorSuite(sensor_noise=True)
        self._wind: WindModel = wind

        self._state: str = ScoutState.IDLE
        self._fire_gps: GPSCoord | None = None
        self._fire_ned: tuple[float, float] | None = None
        self._severity: str = "MEDIUM"

# Orbit state
        self._orbit_radius_m: float = DEFAULT_ORBIT_RADIUS_M
        self._orbit_waypoints: list[GPSCoord] = []
        self._orbit_idx: int = 0
        self._orbit_passes: int = 0

# Grid sweep state
        self._grid_waypoints: list[GPSCoord] = []
        self._grid_idx: int = 0

# Sensor output (updated every tick)
        self.last_sensor_readings: dict[str, float | None] = {}

# Hotspot map
        self._hotspots: list[HotspotDict] = []

# Perimeter estimate (m) -- updated each orbit pass
        self._perimeter_m: float | None = None

# region  PUBLIC API

    @property
    def position_gps(self) -> GPSCoord:
        """Noisy GPS position for telemetry output."""
        return self._movement.position_gps_noisy

    @property
    def position_gps_true(self) -> GPSCoord:
        """True (unfiltered) GPS position for internal use."""
        return self._movement.position_gps

    @property
    def altitude_m_amsl(self) -> float:
        """Current altitude above mean sea level (m)."""
        return self._movement.altitude_m_amsl

    @property
    def heading_deg(self) -> float:
        """Current heading in degrees true."""
        return self._movement.heading_deg

    @property
    def speed_mps(self) -> float:
        """Current ground speed (m/s)."""
        return self._movement.speed_mps

    @property
    def state(self) -> str:
        """Current state machine state string."""
        return self._state

    @property
    def perimeter_estimate_m(self) -> float | None:
        """Estimated fire perimeter (m) based on thermal coverage."""
        return self._perimeter_m

    @property
    def hotspots(self) -> list[HotspotDict]:
        """List of detected thermal hotspots."""
        return list(self._hotspots)

    @property
    def orbit_passes(self) -> int:
        """Number of completed orbit passes."""
        return self._orbit_passes

    def dispatch_to(self, fire_gps: GPSCoord, severity: str = "MEDIUM") -> Self:
        """
        Order scout to investigate fire at fire_gps.

        Transitions: IDLE/any -> TRANSITING.

        Args:
            fire_gps: GPS coordinate of the fire origin.
            severity: Fire severity level (LOW, MEDIUM, HIGH, CRITICAL).

        Returns:
            Self for method chaining.
        """
        self._fire_gps    = fire_gps
        self._severity    = severity
        self._fire_ned    = self._gps_to_fire_ned(fire_gps)
        self._orbit_radius_m = ORBIT_RADIUS_BY_SEVERITY.get(severity, DEFAULT_ORBIT_RADIUS_M)
        self._state       = ScoutState.TRANSITING
        target_gps = GPSCoord(fire_gps.lat_deg, fire_gps.lon_deg, CRUISE_ALTITUDE_M)
        self._movement.set_waypoint(target_gps, speed_mps=TRANSIT_SPEED_MPS)
        if self._fire_ned:
            intensity = {"LOW": 0.2, "MEDIUM": 0.4, "HIGH": 0.7, "CRITICAL": 1.0}.get(severity, 0.4)
            self._wind.set_fire(self._fire_ned, intensity)
        return self

    def start_grid_sweep(self) -> Self:
        """
        Switch from orbit to systematic grid sweep.

        Returns:
            Self for method chaining.
        """
        if self._fire_gps and self._fire_ned:
            self._grid_waypoints = self._build_grid_waypoints()
            self._grid_idx       = 0
            self._state          = ScoutState.GRID_SWEEP
            if self._grid_waypoints:
                self._movement.set_waypoint(self._grid_waypoints[0], speed_mps=GRID_SPEED_MPS)
        return self

    def recall(self) -> Self:
        """
        Return to home base and clear fire from wind model.

        Returns:
            Self for method chaining.
        """
        self._state = ScoutState.RETURNING
        self._movement.return_home(speed_mps=TRANSIT_SPEED_MPS)
        self._wind.clear_fire()
        return self

    def tick(self, dt: float = 2.0) -> None:
        """
        Advance state machine and kinematics by dt seconds.

        Updates self.last_sensor_readings.

        Args:
            dt: Simulation timestep in seconds.
        """
        self._movement.tick(dt)

        if self._state == ScoutState.TRANSITING:
            if self._movement.is_at_waypoint():
                self._start_orbit()

        elif self._state == ScoutState.ORBITING:
            if self._movement.is_at_waypoint():
                self._advance_orbit()

        elif self._state == ScoutState.GRID_SWEEP:
            if self._movement.is_at_waypoint():
                self._advance_grid()

        elif self._state == ScoutState.RETURNING:
            if self._movement.is_at_waypoint():
                self._state = ScoutState.IDLE
                self._wind.clear_fire()

        self._update_sensors(dt)

# endregion

# region  PRIVATE -- orbit management

    def _start_orbit(self) -> None:
        """Build orbit waypoints and begin first leg."""
        if self._fire_gps is None:
            self._state = ScoutState.IDLE
            return
        self._orbit_waypoints = self._build_orbit_waypoints()
        self._orbit_idx       = 0
        self._state           = ScoutState.ORBITING
        self._movement.set_waypoint(self._orbit_waypoints[0], speed_mps=ORBIT_SPEED_MPS)

    def _advance_orbit(self) -> None:
        """Advance to next orbit waypoint; count passes."""
        self._orbit_idx += 1
        if self._orbit_idx >= len(self._orbit_waypoints):
            self._orbit_idx    = 0
            self._orbit_passes += 1
            self._update_perimeter_estimate()
        wp = self._orbit_waypoints[self._orbit_idx]
        self._movement.set_waypoint(wp, speed_mps=ORBIT_SPEED_MPS)

    def _build_orbit_waypoints(self) -> list[GPSCoord]:
        """
        Generate ORBIT_POINTS evenly-spaced waypoints on a great circle
        at self._orbit_radius_m from the fire origin.
        """
        if self._fire_gps is None:
            return []
        wps: list[GPSCoord] = []
        for i in range(ORBIT_POINTS):
            bearing = (360.0 / ORBIT_POINTS) * i
            wp = destination_point(self._fire_gps, bearing, self._orbit_radius_m)
            wps.append(GPSCoord(wp.lat_deg, wp.lon_deg, ORBIT_ALTITUDE_M))
        return wps

    def _update_perimeter_estimate(self) -> None:
        """
        Estimate fire perimeter from thermal coverage and orbit geometry.
        Method: if thermal_coverage_pct = C, estimated fire radius = r_orbit x sqrt(C).
        """
        cover = self.last_sensor_readings.get("thermal_coverage_pct", 0.1)
        r_est = self._orbit_radius_m * math.sqrt(max(0.01, cover))  # pyright: ignore[reportArgumentType]
        self._perimeter_m = round(2 * math.pi * r_est, 1)

# endregion

# region  PRIVATE -- grid management

    def _build_grid_waypoints(self) -> list[GPSCoord]:
        """
        Build a boustrophedon (lawnmower) grid of waypoints centred on fire.
        GRID_LEGS parallel legs, each GRID_STEP_M apart.
        Legs alternate direction for efficiency.
        """
        if self._fire_gps is None:
            return []
        wps: list[GPSCoord] = []
        half = (GRID_LEGS - 1) / 2.0
        for i in range(GRID_LEGS):
            lateral_m = (i - half) * GRID_STEP_M
            leg_half   = GRID_LEGS * GRID_STEP_M / 2
            start_bear = 0.0   if i % 2 == 0 else 180.0
            end_bear   = 180.0 if i % 2 == 0 else 0.0
            lat_origin = destination_point(self._fire_gps, 90.0, lateral_m)
            p1 = destination_point(lat_origin, start_bear, leg_half)
            p2 = destination_point(lat_origin, end_bear,   leg_half)
            wps.append(GPSCoord(p1.lat_deg, p1.lon_deg, ORBIT_ALTITUDE_M))
            wps.append(GPSCoord(p2.lat_deg, p2.lon_deg, ORBIT_ALTITUDE_M))
        return wps

    def _advance_grid(self) -> None:
        """Advance to next grid waypoint or return to orbit."""
        self._grid_idx += 1
        if self._grid_idx >= len(self._grid_waypoints):
            self._start_orbit()
        else:
            self._movement.set_waypoint(
                self._grid_waypoints[self._grid_idx], speed_mps=GRID_SPEED_MPS
            )

# endregion

# region  PRIVATE -- sensor update

    def _update_sensors(self, dt: float) -> None:
        """Compute sensor readings based on current position and fire state."""
        if self._fire_ned is None or self._state not in (
            ScoutState.ORBITING, ScoutState.GRID_SWEEP
        ):
            self.last_sensor_readings = {}
            return

        pos    = self._movement.position_gps
        my_ned = gps_to_ned(
            GPSCoord(pos.lat_deg, pos.lon_deg, 0.0),
            GPSCoord(self._fire_gps.lat_deg, self._fire_gps.lon_deg, 0.0)
            if self._fire_gps else pos
        )
        drone_ned_3d: tuple[float, float, float] = (
            -my_ned[0],
            -my_ned[1],
            max(1.0, self._movement.altitude_m_agl),
        )
        fire_ned_origin: tuple[float, float] = (0.0, 0.0)

        wind_n = self._wind.mean_speed_mps * math.cos(
            math.radians(self._wind.mean_direction_deg + 180)
        )
        wind_e = self._wind.mean_speed_mps * math.sin(
            math.radians(self._wind.mean_direction_deg + 180)
        )

        thermal_peak = self._sensors.thermal_peak_temp_c(
            drone_ned=drone_ned_3d,
            fire_ned=fire_ned_origin,
            fire_severity=self._severity,
        )
        thermal_cov = self._sensors.thermal_coverage_pct(
            drone_ned=drone_ned_3d,
            fire_ned=fire_ned_origin,
            fire_severity=self._severity,
        )
        smoke_mg = self._sensors.smoke_density_mg_m3(
            drone_ned=drone_ned_3d,
            fire_ned=fire_ned_origin,
            fire_severity=self._severity,
            wind_n_mps=wind_n,
            wind_e_mps=wind_e,
        )
        smoke_od = self._sensors.smoke_optical_density(
            drone_ned=drone_ned_3d,
            fire_ned=fire_ned_origin,
            fire_severity=self._severity,
            wind_n_mps=wind_n,
            wind_e_mps=wind_e,
        )
        flame_h = self._sensors.flame_height_m(self._severity)
        dist_f  = self._sensors.distance_to_flame_m(drone_ned_3d, fire_ned_origin)

        self.last_sensor_readings = {
            "thermal_peak_temp_c":    thermal_peak,
            "thermal_coverage_pct":   thermal_cov,
            "smoke_density_mg_m3":    smoke_mg,
            "smoke_optical_density":  smoke_od,
            "flame_height_m":         flame_h,
            "distance_to_flame_m":    dist_f if not math.isnan(dist_f) else None,
            "wind_speed_mps":         round(self._wind.mean_speed_mps, 2),
            "wind_direction_deg":     round(self._wind.mean_direction_deg, 1),
            "perimeter_estimate_m":   self._perimeter_m,
        }

        if thermal_peak > 350.0:
            self._record_hotspot(drone_ned_3d, thermal_peak)

    def _record_hotspot(self, drone_ned: tuple[float, float, float], temp_c: float) -> None:
        """Add thermal hotspot; deduplicate within HOTSPOT_DEDUP_RADIUS_M."""
        for h in self._hotspots:
            hN, hE = h["ned"]
            dN = drone_ned[0] - hN
            dE = drone_ned[1] - hE
            if math.sqrt(dN * dN + dE * dE) < HOTSPOT_DEDUP_RADIUS_M:
                h["peak_temp_c"] = max(h["peak_temp_c"], temp_c)
                h["last_seen"]   = time.time()
                return
        self._hotspots.append({
            "ned":        (drone_ned[0], drone_ned[1]),
            "peak_temp_c": temp_c,
            "first_seen": time.time(),
            "last_seen":  time.time(),
        })

    def _gps_to_fire_ned(self, fire_gps: GPSCoord) -> tuple[float, float]:
        """NED offset of fire from drone home (approximate, used for wind model)."""
        return (0.0, 0.0)

# endregion
