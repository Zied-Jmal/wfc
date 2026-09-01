"""
action/suppression.py - Firefighting Drone Physical Action Engine

Wind-corrected approach, real litre payload, drop ballistics.
State machine:
IDLE TRANSITING APPROACH DROPPING EGRESS RETURNING
"""

from __future__ import annotations

import math
import os
from typing import Final, Self

from action.gps import GPSCoord, destination_point, haversine_distance_m
from action.movement import CRUISE_ALTITUDE_M, DroneMovementEngine
from action.resources import PUMP_FLOW_RATE_L_S, DroneResourceModel
from action.wind import WindModel

# -- Suppression flight parameters (configurable via env vars) ---
DROP_ALTITUDE_M: Final[float] = float(os.getenv("SUPPRESS_DROP_ALTITUDE_M", "20.0"))
APPROACH_SPEED_MPS: Final[float] = float(os.getenv("SUPPRESS_APPROACH_SPEED_MPS", "6.0"))
TRANSIT_SPEED_MPS: Final[float] = float(os.getenv("SUPPRESS_TRANSIT_SPEED_MPS", "14.0"))
EGRESS_SPEED_MPS: Final[float] = float(os.getenv("SUPPRESS_EGRESS_SPEED_MPS", "14.0"))
APPROACH_DISTANCE_M: Final[float] = float(os.getenv("SUPPRESS_APPROACH_DISTANCE_M", "120.0"))
EGRESS_DISTANCE_M: Final[float] = float(os.getenv("SUPPRESS_EGRESS_DISTANCE_M", "100.0"))

# -- Drop ballistics -------------------------------------------------
GRAVITY_MPS2: Final[float] = 9.807
DROP_FALL_TIME_S: Final[float] = math.sqrt(2 * DROP_ALTITUDE_M / GRAVITY_MPS2)

# -- Suppression effectiveness ---------------------------------------
LITRES_PER_M2_BY_SEVERITY: Final[dict[str, float]] = {
    "LOW": 0.5,
    "MEDIUM": 1.2,
    "HIGH": 2.5,
    "CRITICAL": 5.0,
}


class SuppressionState:
    IDLE = "IDLE"
    TRANSITING = "TRANSITING"
    APPROACH = "APPROACH"
    DROPPING = "DROPPING"
    EGRESS = "EGRESS"
    RETURNING = "RETURNING"


class SuppressionActionEngine:
    """
    Physical firefighting drone action engine.

    Integrates:
      - DroneMovementEngine : 3-D kinematics + GPS + collision avoidance
      - WindModel           : for wind-corrected approach offset
      - DroneResourceModel  : litres delivered, battery consumed

    Call tick(dt) from FirefightingDroneNode._telemetry_loop() every 2s.
    Read payload_litres and suppression_effectiveness_pct from properties.
    """

    def __init__(
        self,
        drone_id: str,
        home: GPSCoord,
        wind: WindModel,
        resources: DroneResourceModel,
        payload_type: str = "water",
    ) -> None:
        """
        Initialize the suppression action engine.

        Args:
            drone_id: Unique drone identifier.
            home: Home base GPS coordinate.
            wind: WindModel instance for wind-corrected approach.
            resources: DroneResourceModel for payload and battery tracking.
            payload_type: Suppressant type ("water" or "retardant").
        """
        self._movement: DroneMovementEngine = DroneMovementEngine(
            drone_id=drone_id,
            home=home,
            wind=wind,
            cruise_speed_mps=TRANSIT_SPEED_MPS,
        )
        self._wind: WindModel = wind
        self._resources: DroneResourceModel = resources
        self._payload_type: str = payload_type

        self._state: str = SuppressionState.IDLE
        self._fire_gps: GPSCoord | None = None
        self._severity: str = "MEDIUM"

        # Drop tracking
        self._drop_passes: int = 0
        self._litres_delivered: float = 0.0
        self._last_pass_litres: float = 0.0

        # Approach/egress waypoints (computed from wind on each dispatch)
        self._approach_start: GPSCoord | None = None
        self._egress_target: GPSCoord | None = None

        # Pump state
        self._pump_active: bool = False

        # Effectiveness
        self._fire_area_m2: float | None = None

    # region  PUBLIC API

    @property
    def position_gps(self) -> GPSCoord:
        """Noisy GPS position for telemetry output."""
        return self._movement.position_gps_noisy

    @property
    def altitude_m_amsl(self) -> float:
        """Current altitude above mean sea level (m)."""
        return self._movement.altitude_m_amsl

    @property
    def speed_mps(self) -> float:
        """Current ground speed (m/s)."""
        return self._movement.speed_mps

    @property
    def state(self) -> str:
        """Current state machine state string."""
        return self._state

    @property
    def pump_active(self) -> bool:
        """Whether the suppressant pump is currently active."""
        return self._pump_active

    @property
    def drop_passes(self) -> int:
        """Number of completed drop passes over the fire."""
        return self._drop_passes

    @property
    def litres_delivered(self) -> float:
        """
        Total litres of suppressant delivered to fire (L).

        Returns:
            Rounded value in litres.
        """
        return round(self._litres_delivered, 2)

    @property
    def suppression_effectiveness_pct(self) -> float:
        """
        Estimated suppression effectiveness (0.0-1.0).

        Computed from litres delivered per m² of estimated fire area.
        Saturates at the required litres per m² for this severity.

        Returns:
            Effectiveness fraction (0.0 to 1.0).
        """
        if self._fire_area_m2 is None or self._fire_area_m2 < 0.1:
            return 0.0
        req = LITRES_PER_M2_BY_SEVERITY.get(self._severity, 1.2) * self._fire_area_m2
        return round(min(1.0, self._litres_delivered / max(0.1, req)), 3)

    def dispatch_to(self, fire_gps: GPSCoord, severity: str = "MEDIUM", fire_area_m2: float | None = None) -> Self:
        """
        Order drone to suppress fire at fire_gps.

        Computes approach vector from current wind direction.

        Args:
            fire_gps: GPS coordinate of the fire.
            severity: Fire severity level (LOW, MEDIUM, HIGH, CRITICAL).
            fire_area_m2: Known fire area (m²). If None, estimated from severity.

        Returns:
            Self for method chaining.
        """
        self._fire_gps = fire_gps
        self._severity = severity
        self._fire_area_m2 = fire_area_m2 or _estimate_fire_area(severity)
        self._state = SuppressionState.TRANSITING
        self._pump_active = False

        self._approach_start = self._compute_approach_start(fire_gps)
        transit_gps = GPSCoord(self._approach_start.lat_deg, self._approach_start.lon_deg, CRUISE_ALTITUDE_M)
        self._movement.set_waypoint(transit_gps, speed_mps=TRANSIT_SPEED_MPS)
        return self

    def recall(self) -> Self:
        """
        Leader recalled drone. Stop pump and return home.

        Returns:
            Self for method chaining.
        """
        self._pump_active = False
        self._state = SuppressionState.RETURNING
        self._movement.return_home(speed_mps=TRANSIT_SPEED_MPS)
        return self

    def tick(self, dt: float = 2.0) -> None:
        """
        Advance state machine and kinematics by dt seconds.

        Also ticks resource model with correct phase and pump state.

        Args:
            dt: Simulation timestep in seconds.
        """
        self._movement.tick(dt)

        if self._state == SuppressionState.TRANSITING:
            self._tick_resources(dt, phase="CRUISE")
            if self._movement.is_at_waypoint() and self._approach_start:
                approach_gps = GPSCoord(
                    self._approach_start.lat_deg,
                    self._approach_start.lon_deg,
                    DROP_ALTITUDE_M,
                )
                self._movement.set_waypoint(approach_gps, speed_mps=APPROACH_SPEED_MPS)
                self._state = SuppressionState.APPROACH

        elif self._state == SuppressionState.APPROACH:
            self._tick_resources(dt, phase="SUPPRESS")
            if self._movement.is_at_waypoint() and self._fire_gps:
                fire_drop_gps = GPSCoord(
                    self._fire_gps.lat_deg,
                    self._fire_gps.lon_deg,
                    DROP_ALTITUDE_M,
                )
                self._movement.set_waypoint(fire_drop_gps, speed_mps=APPROACH_SPEED_MPS)
                self._state = SuppressionState.DROPPING
                self._last_pass_litres = 0.0

        elif self._state == SuppressionState.DROPPING:
            fire_dist = haversine_distance_m(self._movement.position_gps, self._fire_gps)  # pyright: ignore[reportArgumentType]

            if not self._pump_active:
                pump_duration = self._resources.payload_litres / PUMP_FLOW_RATE_L_S
                safe_dist = pump_duration * APPROACH_SPEED_MPS + DROP_FALL_TIME_S * APPROACH_SPEED_MPS
                if fire_dist <= safe_dist:
                    self._pump_active = True

            if self._pump_active:
                self._tick_resources(dt, phase="SUPPRESS", pump=True)
                if self._resources.payload_litres >= 0:
                    pass_drain = PUMP_FLOW_RATE_L_S * dt
                    self._last_pass_litres += pass_drain
                    self._litres_delivered += pass_drain

            if self._movement.is_at_waypoint():
                self._pump_active = False
                self._drop_passes += 1
                self._state = SuppressionState.EGRESS
                egress_gps = self._compute_egress_point()
                self._egress_target = egress_gps
                self._movement.set_waypoint(
                    GPSCoord(egress_gps.lat_deg, egress_gps.lon_deg, CRUISE_ALTITUDE_M),
                    speed_mps=EGRESS_SPEED_MPS,
                )

            elif self._resources.payload_litres <= 0.0:
                self._pump_active = False
                self._drop_passes += 1
                self._state = SuppressionState.RETURNING
                self._movement.return_home(speed_mps=TRANSIT_SPEED_MPS)

        elif self._state == SuppressionState.EGRESS:
            self._tick_resources(dt, phase="CRUISE")
            if self._movement.is_at_waypoint():
                if self._resources.low_payload:
                    self._state = SuppressionState.RETURNING
                    self._movement.return_home(speed_mps=TRANSIT_SPEED_MPS)
                else:
                    self._approach_start = self._compute_approach_start(self._fire_gps)
                    approach_gps = GPSCoord(
                        self._approach_start.lat_deg,
                        self._approach_start.lon_deg,
                        DROP_ALTITUDE_M,
                    )
                    self._movement.set_waypoint(approach_gps, speed_mps=APPROACH_SPEED_MPS)
                    self._state = SuppressionState.APPROACH

        elif self._state == SuppressionState.RETURNING:
            self._tick_resources(dt, phase="CRUISE")
            if self._movement.is_at_waypoint():
                self._state = SuppressionState.IDLE
                self._resources.service_at_base()

    # endregion

    # region  PRIVATE

    def _compute_approach_start(self, fire_gps: GPSCoord | None) -> GPSCoord:
        """
        Compute the upwind approach start point.
        The drone approaches FROM upwind: bearing = wind_from_deg.
        Approach starts APPROACH_DISTANCE_M upwind of the fire.
        """
        if fire_gps is None:
            return self._movement.position_gps
        wind_from_deg = self._wind.mean_direction_deg
        approach_bearing = wind_from_deg
        return destination_point(fire_gps, approach_bearing, APPROACH_DISTANCE_M)

    def _compute_egress_point(self) -> GPSCoord:
        """
        Egress point: EGRESS_DISTANCE_M downwind of fire.
        Downwind = wind_from_deg + 180.
        """
        if self._fire_gps is None:
            return self._movement.position_gps
        downwind_bearing = (self._wind.mean_direction_deg + 180.0) % 360.0
        return destination_point(self._fire_gps, downwind_bearing, EGRESS_DISTANCE_M)

    def _tick_resources(self, dt: float, phase: str, pump: bool = False) -> None:
        """
        Forward resource tick with current flight phase.
        Payload drain is handled by DroneResourceModel when pump_active=True.
        """
        self._resources.tick(dt=dt, phase=phase, pump_active=pump, sensors_on=False)


# endregion


def _estimate_fire_area(severity: str) -> float:
    """Empirical fire area estimate (m^2) from severity."""
    return {"LOW": 500.0, "MEDIUM": 2000.0, "HIGH": 8000.0, "CRITICAL": 25000.0}.get(severity, 2000.0)
