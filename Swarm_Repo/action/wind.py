"""
action/wind.py - Atmospheric Wind & Turbulence Model

Dryden continuous-spectrum turbulence + convective plume updrafts.
Provides a physically-continuous wind field that drones fly through.
Wind values are used by:

  - DroneMovementEngine  : drift compensation (wind pushes drone off course)
  - ScoutActionEngine    : upwind approach offset for drop runs
  - ThermalSensor        : smoke transport direction

ISO units:
  wind speed     : m/s
  wind direction : degrees true (°T), 0 = North = wind FROM North (meteorological)
  altitude       : m AMSL
  temperature    : °C
  pressure       : hPa (= mbar)

References:
  Dryden turbulence - MIL-SPEC-1797A, §3.7.2 "Dryden Power Spectral Density"
  Convective plume  - Stull, "An Introduction to Boundary Layer Meteorology", 1988
  Wind shear log-law - ESDU Data Item 85020
"""

from __future__ import annotations

import math
import random
import time

# Atmospheric constants
STANDARD_ATMOSPHERE_HPA = 1013.25  # hPa  (ISA sea-level pressure)
STANDARD_TEMP_SEA_LEVEL_C = 15.0  # °C   (ISA sea-level temperature)
TEMP_LAPSE_RATE_K_PER_M = 0.0065  # K/m  (ISA troposphere lapse rate)
GAS_CONSTANT_DRY_AIR = 287.058  # J/(kg·K)
AIR_DENSITY_SEA_LEVEL = 1.225  # kg/m³  (ISA sea level)

# Dryden turbulence intensity table (MIL-SPEC-1797A 3.7.2)
# σ = turbulence intensity (m/s RMS) at 20 ft AGL for each severity
TURB_INTENSITY = {"NONE": 0.0, "LIGHT": 0.5, "MODERATE": 1.5, "SEVERE": 3.0}

# Dryden length scales (m)
DRYDEN_L_u = 533.0  # longitudinal length scale (m)   MIL-SPEC value at medium alt
DRYDEN_L_w = 533.0  # vertical length scale (m)


class WindModel:
    """
    Continuous atmospheric wind model for a single zone/simulation.

    Shared by all drones in the same zone.  Each tick advances:
      1. Mean wind  - slow random walk (τ = 30 s)
      2. Dryden turbulence  - coloured noise shaping
      3. Fire convective plume  - updraft & outward flow near fire

    Usage:
        wind = WindModel(mean_speed_mps=5.0, mean_dir_deg=225.0)
        # Every 2s in the simulation loop:
        wx, wy, wz = wind.tick(dt=2.0, fire_pos_ned=(100, 200), drone_ned=(80, 180, 40))
        # wx = North component (m/s), wy = East, wz = Up (positive = rising)
    """

    def __init__(
        self,
        mean_speed_mps: float = 5.0,  # m/s  - initial mean horizontal speed
        mean_dir_deg: float = 225.0,  # °T   - wind blowing FROM this direction
        turbulence: str = "LIGHT",
        altitude_ref_m: float = 10.0,  # m    - reference altitude for log-law
    ):
        self._mean_speed = mean_speed_mps  # m/s
        self._mean_dir_deg = mean_dir_deg  # °T meteorological
        self._turb_sigma = TURB_INTENSITY.get(turbulence, 0.5)
        self._alt_ref = altitude_ref_m

        # Dryden state (first-order Gauss-Markov processes)
        # u = turbulence in North axis, v = East axis, w = vertical
        self._u_turb = 0.0
        self._v_turb = 0.0
        self._w_turb = 0.0

        # Slow mean-wind random walk state
        self._rw_speed = mean_speed_mps
        self._rw_dir = mean_dir_deg

        # Plume state
        self._fire_pos_ned: tuple[float, float] | None = None  # (north, east) m
        self._fire_intensity = 0.0  # 0.0-1.0 scale

        self._last_tick = time.monotonic()

    # region  PUBLIC API

    def set_fire(self, pos_ned: tuple[float, float], intensity: float) -> None:
        """
        Inform the wind model of an active fire's NED position and
        intensity (0.0-1.0). Enables convective plume effects.
        """
        self._fire_pos_ned = pos_ned
        self._fire_intensity = max(0.0, min(1.0, intensity))

    def clear_fire(self) -> None:
        self._fire_pos_ned = None
        self._fire_intensity = 0.0

    def tick(
        self,
        dt: float,
        drone_ned: tuple[float, float, float],  # (north_m, east_m, alt_m AGL)
    ) -> tuple[float, float, float]:
        """
        Advance wind model and return instantaneous wind at drone position.

        Returns:
            (v_north_mps, v_east_mps, v_up_mps)
            Positive north = blowing North, positive east = blowing East,
            positive up = rising air (updraft).
        """
        # 1 - Mean wind slow random walk (τ = 60 s, σ = 0.3 m/s per √s)
        tau_wind = 60.0
        sigma_rw = 0.3
        self._rw_speed += (-self._rw_speed / tau_wind + sigma_rw * _white_noise()) * dt
        self._rw_speed = max(0.0, min(25.0, self._rw_speed))  # clamp to 0-25 m/s
        self._rw_dir += _white_noise() * 2.0 * dt  # drift ±2°/s²
        self._rw_dir = self._rw_dir % 360.0

        # Convert met convention (FROM) to math (velocity vector TO)
        dir_to_deg = (self._rw_dir + 180.0) % 360.0
        _dir_rad = math.radians(dir_to_deg)
        u_mean = self._rw_speed * math.cos(math.radians(dir_to_deg - 90))  # North
        v_mean = self._rw_speed * math.sin(math.radians(dir_to_deg - 90))  # East

        # 2 - Wind shear (log-law): speed increases with altitude
        # V(z) / V(z_ref) = log(z/z0) / log(z_ref/z0)  where z0 = roughness length ≈ 0.03 m
        alt_agl = max(1.0, drone_ned[2])
        Z0 = 0.03  # m  aerodynamic roughness length (open terrain)
        denom = math.log(max(0.1, self._alt_ref) / Z0)
        shear = 1.0 if abs(denom) < 1e-06 else math.log(alt_agl / Z0) / denom
        shear = max(0.3, min(2.5, shear))  # clamp
        u_mean *= shear
        v_mean *= shear

        # 3 - Dryden turbulence (first-order Gauss-Markov)
        # Time constant τ = L / V (L = length scale, V ≈ mean speed)
        V = max(1.0, self._rw_speed)
        tau_u = DRYDEN_L_u / V
        tau_w = DRYDEN_L_w / V
        sigma_u = self._turb_sigma * math.sqrt(2 * dt / tau_u)
        sigma_w = self._turb_sigma * math.sqrt(2 * dt / tau_w) * 0.5

        self._u_turb += (-self._u_turb / tau_u) * dt + sigma_u * _white_noise()
        self._v_turb += (-self._v_turb / tau_u) * dt + sigma_u * _white_noise()
        self._w_turb += (-self._w_turb / tau_w) * dt + sigma_w * _white_noise()

        # 4 - Convective plume near fire
        u_plume = 0.0
        v_plume = 0.0
        w_plume = 0.0
        if self._fire_pos_ned is not None and self._fire_intensity > 0:
            fn, fe = self._fire_pos_ned
            dn = drone_ned[0] - fn
            de = drone_ned[1] - fe
            r = math.sqrt(dn * dn + de * de) + 0.1  # metres from fire centre
            # Updraft: peaks at centre, decays with r (Gaussian bell)
            plume_radius = 40.0 + self._fire_intensity * 60.0  # 40-100 m
            updraft_peak = self._fire_intensity * 8.0  # up to 8 m/s updraft
            w_plume = updraft_peak * math.exp(-0.5 * (r / plume_radius) ** 2)
            # Outward horizontal inflow (conservation of mass)
            inflow_speed = updraft_peak * 0.4 * (1 - math.exp(-r / plume_radius))
            if r > 1.0:
                u_plume = -inflow_speed * (dn / r)  # toward fire centre
                v_plume = -inflow_speed * (de / r)

        # Sum all components
        v_north = u_mean + self._u_turb + u_plume
        v_east = v_mean + self._v_turb + v_plume
        v_up = self._w_turb + w_plume

        return (
            round(v_north, 3),
            round(v_east, 3),
            round(v_up, 3),
        )

    @property
    def mean_speed_mps(self) -> float:
        return round(self._rw_speed, 2)

    @property
    def mean_direction_deg(self) -> float:
        """Meteorological: direction wind is blowing FROM (°T)."""
        return round(self._rw_dir % 360.0, 1)

    @property
    def turbulence_level(self) -> str:
        s = self._turb_sigma
        if s < 0.3:
            return "NONE"
        if s < 1.0:
            return "LIGHT"
        if s < 2.0:
            return "MODERATE"
        return "SEVERE"

    def air_density_kg_m3(self, alt_m: float) -> float:
        """
        ISA air density at altitude alt_m (m AMSL).
        Used by resource model for accurate thrust/power calculation.
        ρ = P / (R_d × T)
        """
        T_K = (STANDARD_TEMP_SEA_LEVEL_C + 273.15) - TEMP_LAPSE_RATE_K_PER_M * alt_m
        P_Pa = STANDARD_ATMOSPHERE_HPA * 100 * (T_K / 288.15) ** 5.2561
        return P_Pa / (GAS_CONSTANT_DRY_AIR * T_K)

    # endregion


def _white_noise() -> float:
    """Unit Gaussian white noise sample via Box-Muller."""
    u1 = max(1e-10, random.random())
    u2 = random.random()
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
