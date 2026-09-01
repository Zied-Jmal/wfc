"""
action/movement.py - 3D Kinematic Drone Movement Engine

Full 3-D point-mass kinematics, WGS-84 GPS, wind drift,
collision avoidance, realistic acceleration/deceleration.

Physics model: point-mass with:
  - Drag force  F_drag = ½ ρ C_d A v²
  - Thrust limited by max_thrust_N
  - Gravity compensation always on
  - 3-D velocity vector (v_north, v_east, v_up) in m/s
  - Position in WGS-84 (lat, lon, alt_m AMSL)
  - NED frame used internally for kinematics

Collision avoidance:
  - Each drone registers its position in the shared SwarmPositionRegistry
  - On every tick, checks all peers within SEPARATION_RADIUS_M
  - If conflict detected: applies repulsive acceleration perpendicular
    to the collision axis (potential-field method)

ISO units used throughout (all documented inline):
  position  : decimal degrees lat/lon, metres AMSL altitude
  velocity  : m/s
  force     : N
  mass      : kg
  time      : s
  energy    : Wh (watt-hours), W (watts)
  distance  : m
  angle     : degrees (°), radians where math requires

Reference drone specs (DJI Matrice 300 RTK class):
  Mass         : 9.0 kg (with payload)
  Max speed    : 23 m/s horizontal, 6 m/s vertical
  Max thrust   : ~180 N (4 × 45 N motors)
  Drag coeff   : C_d ~ 1.1 (bluff body approximation)
  Frontal area : A ~ 0.10 m2
  Battery      : 13.2 Ah x 44.4 V = 585.9 Wh
"""

from __future__ import annotations

import math
import threading

from action.gps import (
    GPSCoord,
    add_gps_noise,
    gps_to_ned,
    haversine_distance_m,
    ned_to_gps,
)
from action.wind import WindModel

# region  Physical constants and drone spec

# Kinematic limits
MAX_HORIZONTAL_SPEED_MPS = 16.0  # m/s  cruise limit (< 23 m/s structural max)
MAX_VERTICAL_SPEED_MPS = 4.0  # m/s  ascent / descent
MAX_HORIZONTAL_ACCEL_MPS2 = 3.5  # m/s² horizontal acceleration
MAX_VERTICAL_ACCEL_MPS2 = 2.0  # m/s² vertical acceleration
HOVER_SPEED_MPS = 0.0  # m/s  hovering

# Airframe (DJI Matrice 300 class)
DRONE_MASS_KG = 9.0  # kg   total flying mass (frame + battery + payload)
DRAG_COEFFICIENT = 1.1  # dimensionless  bluff-body approximation
FRONTAL_AREA_M2 = 0.10  # m²   projected frontal area
GRAVITY_MPS2 = 9.807  # m/s²

# Altitude operational envelope
ALTITUDE_FLOOR_M = 30.0  # m AMSL  hard floor
ALTITUDE_CEILING_M = 150.0  # m AMSL  hard ceiling
CRUISE_ALTITUDE_M = 80.0  # m AMSL  default transit altitude
SENSOR_ALTITUDE_M = 40.0  # m AMSL  altitude for sensor passes

# Arrival threshold
WAYPOINT_ARRIVAL_RADIUS_M = 5.0  # m  horizontal distance to trigger arrival

# Collision avoidance
SEPARATION_RADIUS_M = 20.0  # m  minimum separation between drones
AVOIDANCE_GAIN = 4.0  # m/s² repulsion acceleration per conflict

# GPS noise
GPS_SIGMA_HORIZONTAL_M = 1.5  # m  UBlox M8N horizontal 1-σ
GPS_SIGMA_VERTICAL_M = 2.5  # m  UBlox M8N vertical 1-σ

# endregion


# region  SwarmPositionRegistry  (module-level singleton)


class _SwarmPositionRegistry:
    """
    Thread-safe registry of all drone NED positions.
    Used by collision avoidance in DroneMovementEngine.
    One global instance shared by all drones in the process.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._positions: dict[str, tuple[float, float, float]] = {}  # {drone_id: (N, E, D_m)}

    def update(self, drone_id: str, ned: tuple[float, float, float]) -> None:
        with self._lock:
            self._positions[drone_id] = ned

    def remove(self, drone_id: str) -> None:
        with self._lock:
            self._positions.pop(drone_id, None)

    def peers(self, drone_id: str) -> list[tuple[str, tuple[float, float, float]]]:
        with self._lock:
            return [(d, p) for d, p in self._positions.items() if d != drone_id]


SWARM_REGISTRY = _SwarmPositionRegistry()

# endregion


# region  DroneMovementEngine


class DroneMovementEngine:
    """
    Full 3-D kinematic movement engine for a single drone.

    Coordinate systems:
      GPS (external) - WGS-84 (lat_deg, lon_deg, alt_m AMSL)
      NED (internal) - North, East, Down metres from home
                       Down is positive toward ground, so alt = home_alt - ned_D

    Kinematics model (per axis, Newton's 2nd law):
      a = (F_thrust - F_drag - F_gravity_component) / mass
      v += a * dt
      pos += v * dt + 0.5 * a * dt²   (Verlet integration)

    Wind interaction:
      Drift = wind_velocity * dt added to position every tick.
      Autopilot corrects against drift by sensing GPS position vs. expected.

    Collision avoidance:
      Potential-field repulsion: if any peer is within SEPARATION_RADIUS_M,
      apply repulsive acceleration away from that peer proportional to 1/r².
    """

    def __init__(
        self,
        drone_id: str,
        home: GPSCoord,
        wind: WindModel,
        cruise_speed_mps: float = MAX_HORIZONTAL_SPEED_MPS,
        cruise_altitude_m: float = CRUISE_ALTITUDE_M,
        gps_noise: bool = True,
    ):
        self._id = drone_id
        self._home = home
        self._wind = wind
        self._cruise_speed = cruise_speed_mps
        self._gps_noise = gps_noise

        # State - internal NED (metres from home)
        # NED: North (+), East (+), Down (+, so alt decreases as D increases)
        self._ned = [0.0, 0.0, -(cruise_altitude_m - home.alt_m)]  # at home, at cruise alt
        # Velocity in NED (m/s)
        self._vel = [0.0, 0.0, 0.0]  # [v_north, v_east, v_down]
        # Target waypoint in NED
        self._wp_ned: list[float] | None = None
        # Desired speed toward waypoint
        self._target_speed = cruise_speed_mps
        # Heading (°T) - direction drone is pointing
        self._heading_deg = 0.0
        # Is engine active
        self._is_moving = False

        # Register in swarm registry
        SWARM_REGISTRY.update(drone_id, (self._ned[0], self._ned[1], self._ned[2]))

    # region  PUBLIC API

    @property
    def position_gps(self) -> GPSCoord:
        """True GPS position (no noise). Use for internal calculations."""
        gps = ned_to_gps(self._home, north_m=self._ned[0], east_m=self._ned[1], down_m=self._ned[2])
        return gps

    @property
    def position_gps_noisy(self) -> GPSCoord:
        """
        GPS position with simulated receiver noise (σ_h = 1.5 m, σ_v = 2.5 m).
        Use this for telemetry - this is what a real drone would report.
        """
        true_pos = self.position_gps
        if self._gps_noise:
            return add_gps_noise(true_pos, GPS_SIGMA_HORIZONTAL_M, GPS_SIGMA_VERTICAL_M)
        return true_pos

    @property
    def altitude_m_amsl(self) -> float:
        """Altitude above mean sea level (m AMSL)."""
        return round(self._home.alt_m - self._ned[2], 2)

    @property
    def altitude_m_agl(self) -> float:
        """
        Altitude above ground level (m AGL).
        Approximated as alt_AMSL − home_alt_AMSL.
        In production: use terrain elevation model (DEM).
        """
        return round(self.altitude_m_amsl - self._home.alt_m, 2)

    @property
    def heading_deg(self) -> float:
        """Drone heading, degrees true (0 = North, 90 = East)."""
        return round(self._heading_deg, 1)

    @property
    def speed_mps(self) -> float:
        """Instantaneous horizontal ground speed (m/s)."""
        return round(math.sqrt(self._vel[0] ** 2 + self._vel[1] ** 2), 2)

    @property
    def vertical_speed_mps(self) -> float:
        """Vertical speed (m/s). Positive = climbing (NED down is negative velocity)."""
        return round(-self._vel[2], 2)

    @property
    def is_moving(self) -> bool:
        return self._is_moving

    def set_waypoint(self, target: GPSCoord, speed_mps: float | None = None) -> None:
        """
        Set navigation target. Converts GPS to internal NED.
        speed_mps defaults to cruise speed if None.
        """
        ned = gps_to_ned(self._home, target)
        self._wp_ned = [ned[0], ned[1], ned[2]]
        self._target_speed = speed_mps if speed_mps is not None else self._cruise_speed
        self._is_moving = True

    def set_waypoint_ned(
        self, north_m: float, east_m: float, alt_m_amsl: float, speed_mps: float | None = None
    ) -> None:
        """Convenience: set waypoint by NED + absolute altitude."""
        down_m = -(alt_m_amsl - self._home.alt_m)
        self._wp_ned = [north_m, east_m, down_m]
        self._target_speed = speed_mps if speed_mps is not None else self._cruise_speed
        self._is_moving = True

    def hover(self) -> None:
        """Command drone to hold position."""
        self._wp_ned = None
        self._is_moving = False

    def return_home(self, speed_mps: float | None = None) -> None:
        """Navigate back to home position at current cruise altitude."""
        home_down = -(CRUISE_ALTITUDE_M - self._home.alt_m)
        self._wp_ned = [0.0, 0.0, home_down]
        self._target_speed = speed_mps if speed_mps is not None else self._cruise_speed
        self._is_moving = True

    def is_at_waypoint(self) -> bool:
        """True when within WAYPOINT_ARRIVAL_RADIUS_M horizontally."""
        if self._wp_ned is None:
            return True
        dN = self._wp_ned[0] - self._ned[0]
        dE = self._wp_ned[1] - self._ned[1]
        return math.sqrt(dN * dN + dE * dE) <= WAYPOINT_ARRIVAL_RADIUS_M

    def distance_to_waypoint_m(self) -> float:
        """Horizontal distance to current waypoint (m). 0 if no waypoint."""
        if self._wp_ned is None:
            return 0.0
        dN = self._wp_ned[0] - self._ned[0]
        dE = self._wp_ned[1] - self._ned[1]
        return math.sqrt(dN * dN + dE * dE)

    def distance_to_gps_m(self, target: GPSCoord) -> float:
        """Horizontal great-circle distance to a GPS point (m)."""
        return haversine_distance_m(self.position_gps, target)

    def tick(self, dt: float) -> None:
        """
                Advance kinematics by dt seconds.
        Steps:
        1. Compute desired velocity vector toward waypoint
        2. Compute thrust acceleration to achieve that velocity
        3. Apply aerodynamic drag
        4. Apply wind drift
        5. Apply collision avoidance repulsion
        6. Integrate velocity position (Verlet)
        7. Clamp altitude to operational envelope
        8. Update swarm registry
        """
        drone_ned_tuple = (self._ned[0], self._ned[1], self.altitude_m_agl)
        wind_N, wind_E, wind_U = self._wind.tick(dt=dt, drone_ned=drone_ned_tuple)

        # 1. Desired velocity toward waypoint
        if self._wp_ned is not None and not self.is_at_waypoint():
            dN = self._wp_ned[0] - self._ned[0]
            dE = self._wp_ned[1] - self._ned[1]
            dD = self._wp_ned[2] - self._ned[2]
            dist_h = math.sqrt(dN * dN + dE * dE)

            # Slow down within 3× arrival radius (deceleration zone)
            decel_dist = 3 * WAYPOINT_ARRIVAL_RADIUS_M
            speed_scale = min(1.0, dist_h / decel_dist) if dist_h < decel_dist else 1.0
            h_speed = self._target_speed * speed_scale

            if dist_h > 0.1:
                v_N_des = h_speed * (dN / dist_h)
                v_E_des = h_speed * (dE / dist_h)
                self._heading_deg = math.degrees(math.atan2(dE, dN)) % 360.0
            else:
                v_N_des = 0.0
                v_E_des = 0.0

            # Vertical: climb/descend at limited rate
            v_D_des = max(-MAX_VERTICAL_SPEED_MPS, min(MAX_VERTICAL_SPEED_MPS, dD / max(dt, 1.0)))
        else:
            # Hovering
            v_N_des = 0.0
            v_E_des = 0.0
            v_D_des = 0.0
            if self.is_at_waypoint() and self._wp_ned is not None:
                # Snap to waypoint
                self._ned[0] = self._wp_ned[0]
                self._ned[1] = self._wp_ned[1]
                self._ned[2] = self._wp_ned[2]
                self._wp_ned = None
                self._is_moving = False

        # 2. Acceleration toward desired velocity (P-controller)
        # Horizontal
        err_N = v_N_des - self._vel[0]
        err_E = v_E_des - self._vel[1]
        err_D = v_D_des - self._vel[2]

        Kp_h = 2.0  # horizontal proportional gain (s⁻¹)
        Kp_v = 1.5  # vertical proportional gain

        a_N = _clamp(Kp_h * err_N, -MAX_HORIZONTAL_ACCEL_MPS2, MAX_HORIZONTAL_ACCEL_MPS2)
        a_E = _clamp(Kp_h * err_E, -MAX_HORIZONTAL_ACCEL_MPS2, MAX_HORIZONTAL_ACCEL_MPS2)
        a_D = _clamp(Kp_v * err_D, -MAX_VERTICAL_ACCEL_MPS2, MAX_VERTICAL_ACCEL_MPS2)

        # 3. Aerodynamic drag (F = ½ ρ C_d A v²)
        rho = self._wind.air_density_kg_m3(self.altitude_m_amsl)  # kg/m³
        v_sq_h = self._vel[0] ** 2 + self._vel[1] ** 2
        if v_sq_h > 0.01:
            drag_mag = 0.5 * rho * DRAG_COEFFICIENT * FRONTAL_AREA_M2 * v_sq_h
            drag_acc = drag_mag / DRONE_MASS_KG  # m/s²
            v_h_mag = math.sqrt(v_sq_h)
            a_N -= drag_acc * (self._vel[0] / v_h_mag)
            a_E -= drag_acc * (self._vel[1] / v_h_mag)

        # 4. Wind drift - autopilot partially corrects
        # Wind pushes drone; GPS-based autopilot corrects ~70%
        # Net drift = 30% of wind (residual uncorrected drift)
        drift_frac = 0.30
        wind_drift_N = wind_N * dt * drift_frac
        wind_drift_E = wind_E * dt * drift_frac

        # 5. Collision avoidance (potential field repulsion)
        ca_N, ca_E = self._collision_avoidance_accel()
        a_N += ca_N
        a_E += ca_E

        # 6. Integrate: velocity and position
        # Verlet integration: x(t+dt) = x(t) + v(t)*dt + 0.5*a*dt²
        self._ned[0] += self._vel[0] * dt + 0.5 * a_N * dt**2 + wind_drift_N
        self._ned[1] += self._vel[1] * dt + 0.5 * a_E * dt**2 + wind_drift_E
        self._ned[2] += self._vel[2] * dt + 0.5 * a_D * dt**2
        # wind vertical (updraft raises drone slightly)
        self._ned[2] -= wind_U * dt * drift_frac  # NED down decreases with updraft

        self._vel[0] = _clamp(self._vel[0] + a_N * dt, -MAX_HORIZONTAL_SPEED_MPS, MAX_HORIZONTAL_SPEED_MPS)
        self._vel[1] = _clamp(self._vel[1] + a_E * dt, -MAX_HORIZONTAL_SPEED_MPS, MAX_HORIZONTAL_SPEED_MPS)
        self._vel[2] = _clamp(self._vel[2] + a_D * dt, -MAX_VERTICAL_SPEED_MPS, MAX_VERTICAL_SPEED_MPS)

        # 7. Altitude clamping
        alt = self.altitude_m_amsl
        if alt < ALTITUDE_FLOOR_M:
            self._ned[2] = -(ALTITUDE_FLOOR_M - self._home.alt_m)
            self._vel[2] = max(0.0, self._vel[2])  # stop downward velocity
        elif alt > ALTITUDE_CEILING_M:
            self._ned[2] = -(ALTITUDE_CEILING_M - self._home.alt_m)
            self._vel[2] = min(0.0, self._vel[2])  # stop upward velocity

        # 8. Update swarm registry
        SWARM_REGISTRY.update(self._id, (self._ned[0], self._ned[1], self._ned[2]))

    def shutdown(self) -> None:
        """Deregister from collision avoidance registry."""
        SWARM_REGISTRY.remove(self._id)

    # endregion

    # region  PRIVATE

    def _collision_avoidance_accel(self) -> tuple[float, float]:
        """
        Potential-field repulsion from nearby drones.
        Returns (a_north, a_east) acceleration components (m/s²).
        """
        a_N = 0.0
        a_E = 0.0
        my_N, my_E, _my_D = self._ned

        for _peer_id, (pN, pE, _pD) in SWARM_REGISTRY.peers(self._id):
            dN = my_N - pN
            dE = my_E - pE
            dist = math.sqrt(dN * dN + dE * dE)
            if 0.1 < dist < SEPARATION_RADIUS_M:
                # Repulsion proportional to 1/dist²
                mag = AVOIDANCE_GAIN * (1.0 - dist / SEPARATION_RADIUS_M) / dist
                a_N += mag * dN / dist
                a_E += mag * dE / dist

        # Clamp avoidance contribution
        a_N = _clamp(a_N, -MAX_HORIZONTAL_ACCEL_MPS2, MAX_HORIZONTAL_ACCEL_MPS2)
        a_E = _clamp(a_E, -MAX_HORIZONTAL_ACCEL_MPS2, MAX_HORIZONTAL_ACCEL_MPS2)
        return a_N, a_E


# endregion

# endregion


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
