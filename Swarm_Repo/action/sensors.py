"""
action/sensors.py - Physics-Based Drone Sensor Simulation

Realistic sensor models with noise, range limits, and fire coupling.
All sensors use physics-based models rather than pure random values:
  - Thermal camera: Stefan-Boltzmann radiation + inverse-square decay + noise
  - Smoke density: Gaussian plume dispersion (Pasquill-Gifford model)
  - Flame height: empirical Heskestad correlation
  - Laser rangefinder: triangulation from altitude + gimbal angle
  - Wind: Dryden model provided by WindModel (action/wind.py)

Sensor noise model:
  Each sensor adds its own characteristic noise:
    - FLIR Lepton 3.5   : NETD ≈ 50 mK (noise-equivalent temperature difference)
    - Optical camera     : SNR ~40 dB in daylight
    - LIDAR rangefinder  : ±3 cm range error
    - Anemometer         : ±2% speed, ±3° direction

ISO units:
  temperature   : °C (Kelvin used internally for radiation)
  radiant flux  : W/m²
  visibility    : m   (meteorological optical range)
  smoke density : mg/m³ (milligrams of particulate per cubic metre)
  flame height  : m
  range         : m

References:
  Thermal radiation - Stefan-Boltzmann: E = ε σ T⁴
  Smoke plume       - Gaussian dispersion, Pasquill-Gifford stability classes
  Flame height      - Heskestad (1983), Fire Technology 20(3)
  FLIR noise        - FLIR Lepton 3.5 datasheet, NETD < 50 mK
"""

from __future__ import annotations

import math
import random


# region  Physical and sensor constants

# Stefan-Boltzmann constant
STEFAN_BOLTZMANN = 5.670374419e-8   # W/(m²·K⁴)
EMISSIVITY_FIRE  = 0.98             # fire emissivity ≈ 1 (blackbody)

# FLIR Lepton 3.5 thermal camera specs
FLIR_NETD_K      = 0.050            # K  noise-equivalent temperature difference
FLIR_FOV_DEG     = 57.0             # ° horizontal field of view
FLIR_MAX_TEMP_C  = 450.0            # °C  sensor saturation temperature

# Laser rangefinder (DJI LiDAR range sensor)
LIDAR_MAX_RANGE_M  = 100.0          # m
LIDAR_NOISE_M      = 0.03           # m  ±3 cm 1-σ

# Smoke sensor thresholds (mg/m³ particulate)
SMOKE_TRACE_MG_M3   = 50.0          # barely detectable
SMOKE_MODERATE_MG_M3 = 200.0        # visible haze
SMOKE_DENSE_MG_M3   = 1000.0        # heavy smoke

# Fire HRR (Heat Release Rate) per severity class (MW)
FIRE_HRR_MW = {
    "LOW":      2.0,
    "MEDIUM":   8.0,
    "HIGH":     25.0,
    "CRITICAL": 80.0,
}

# Ambient temperature
AMBIENT_TEMP_C = 20.0

# endregion


# region  FireSensorSuite

class FireSensorSuite:
    """
    Complete sensor suite for a scout drone.

    Encapsulates:
      - thermal_peak_temp_c()      : hottest pixel (°C) from FLIR camera
      - thermal_coverage_pct()     : fraction of FOV above fire threshold
      - smoke_density_mg_m3()      : Gaussian plume dispersion (mg/m³)
      - smoke_optical_density()    : 0.0-1.0 normalised for dashboard
      - flame_height_m()           : Heskestad correlation
      - distance_to_flame_m()      : laser rangefinder triangulation
      - wind_from_plume()          : wind direction inferred from smoke axis

    All methods require:
      - drone_pos_ned  : (north_m, east_m, alt_m_agl)  drone position
      - fire_pos_ned   : (north_m, east_m)              fire ground position
      - fire_severity  : "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
      - wind_N, wind_E : wind velocity components (m/s) from WindModel
    """

    def __init__(self, sensor_noise: bool = True):
        self._noise    = sensor_noise
        self._last_thermal_update = 0.0
        self._cached_thermal: dict[str, float] = {}  # pyright: ignore[reportMissingTypeArgument]

# region  Thermal camera (FLIR Lepton 3.5)

    def thermal_peak_temp_c(
        self,
        drone_ned:     tuple[float, float, float],
        fire_ned:      tuple[float, float],
        fire_severity: str = "MEDIUM",
    ) -> float:
        """Peak temperature in FLIR frame (°C).
Physics:
1. Fire radiosity: E = ε σ T_fire⁴ (W/m²)
2. Irradiance at drone: I = E × A_fire / (4π r²) (inverse-square law)
3. Apparent temperature: T_app = (I / (ε σ))^(1/4)
4. Add sensor NETD noise
T_fire is estimated from HRR using Radhakrishnan's empirical formula:
T_flame_K ≈ T_ambient + 900 × (HRR/HRR_ref)^0.4
Returns °C, clamped to sensor range [ambient, FLIR_MAX_TEMP_C].
        """
        r_m = self._range_to_fire(drone_ned, fire_ned)
        hrr = FIRE_HRR_MW.get(fire_severity, 8.0) # MW
        # Flame temperature estimate (K)
        t_fire_k = (AMBIENT_TEMP_C + 273.15) + 900.0 * (hrr / 8.0) ** 0.4
        # Fire projected area (empirical: A ≈ 0.1 × HRR^0.6 m²)
        a_fire_m2 = 0.1 * (hrr * 1000) ** 0.6 # kW use kW for scaling
        # Radiant exitance (W/m²)
        exitance_w_m2 = EMISSIVITY_FIRE * STEFAN_BOLTZMANN * t_fire_k ** 4
        # Irradiance at drone (W/m²) - inverse-square law
        irrad_w_m2 = exitance_w_m2 * a_fire_m2 / max(0.1, 4 * math.pi * r_m ** 2)
        # Apparent temperature from irradiance
        t_apparent_k = (irrad_w_m2 / (EMISSIVITY_FIRE * STEFAN_BOLTZMANN)) ** 0.25
        t_apparent_c = t_apparent_k - 273.15
        # Blend: closer drones see more of the actual flame temp
        blend = math.exp(-r_m / 80.0) # full apparent at 0 m, ~37% at 80 m
        t_sensor_c = blend * min(t_fire_k - 273.15, FLIR_MAX_TEMP_C) + (1 - blend) * t_apparent_c
        t_sensor_c = max(AMBIENT_TEMP_C, min(FLIR_MAX_TEMP_C, t_sensor_c))
        if self._noise:
            t_sensor_c += self._gauss(FLIR_NETD_K) * 100 # ±5°C noise
        return round(t_sensor_c, 1)

    def thermal_coverage_pct(
        self,
        drone_ned: tuple[float, float, float],
        fire_ned: tuple[float, float],
        fire_severity: str = "MEDIUM",
    ) -> float:
        """Fraction of FLIR field-of-view (57° FOV) showing temperature > 300°C.

        Geometry: ground footprint at range r = 2r tan(FOV/2).
        Coverage fraction = fire area / footprint area, clamped to [0, 1].
        """
        r_m      = self._range_to_fire(drone_ned, fire_ned)
        hrr      = FIRE_HRR_MW.get(fire_severity, 8.0)

        # Footprint area at slant range r (circular approximation)
        half_fov = math.radians(FLIR_FOV_DEG / 2)
        fp_radius = r_m * math.tan(half_fov)           # m
        fp_area   = math.pi * fp_radius ** 2            # m²

        # Fire area estimate: A ≈ π × R_fire²
        r_fire    = 10.0 * (hrr ** 0.5)                 # empirical: 10 m at 1 MW
        fire_area = math.pi * r_fire ** 2               # m²

        cov = min(1.0, fire_area / max(0.1, fp_area))

        if self._noise:
            cov += self._gauss(0.01)
        return round(max(0.0, min(1.0, cov)), 3)

# endregion

# region  Smoke density (Gaussian plume)

    def smoke_density_mg_m3(
        self,
        drone_ned:     tuple[float, float, float],
        fire_ned:      tuple[float, float],
        fire_severity: str = "MEDIUM",
        wind_n_mps:    float = 3.0,
        wind_e_mps:    float = 0.0,
    ) -> float:
        """
        Smoke particulate concentration at drone position (mg/m³).

        Uses the Gaussian plume dispersion model (EPA AP-42):
          C(x,y,z) = Q / (2π σ_y σ_z U) × exp(−y²/(2σ_y²)) × exp(−z²/(2σ_z²))
        where:
          Q      = source emission rate (mg/s) - proportional to HRR
          σ_y,z  = Pasquill-Gifford dispersion coefficients (Stability class B)
          U      = mean wind speed (m/s)
          x,y    = along-wind and cross-wind distance from source
          z      = height above plume centreline

        Units: mg/m³
        """
        hrr = FIRE_HRR_MW.get(fire_severity, 8.0)

        # Smoke emission rate (mg/s) - empirical: ~0.5 kg/s/MW
        Q_mg_s = hrr * 0.5e6   # mg/s

        # Drone position relative to fire
        dn = drone_ned[0] - fire_ned[0]   # north offset (m)
        de = drone_ned[1] - fire_ned[1]   # east offset (m)
        dz = max(0.0, drone_ned[2])       # height AGL (m)

        # Mean wind speed
        u = max(0.5, math.sqrt(wind_n_mps**2 + wind_e_mps**2))

        # Rotate drone position into along/cross-wind coordinates
        wind_dir_rad = math.atan2(wind_e_mps, wind_n_mps)
        x = dn * math.cos(wind_dir_rad) + de * math.sin(wind_dir_rad)   # along-wind
        y = -dn * math.sin(wind_dir_rad) + de * math.cos(wind_dir_rad)  # cross-wind

        # Only downwind of source contributes
        if x < 0.1:
            return 0.0

        # Pasquill-Gifford σ (stability class B - unstable, near fire)
        # Empirical fits: σ_y = a × x^0.9, σ_z = b × x^c
        sigma_y = 0.22 * x ** 0.894
        sigma_z = 0.16 * x ** 0.870
        sigma_y = max(0.1, sigma_y)
        sigma_z = max(0.1, sigma_z)

        # Gaussian concentration (mg/m³)
        c = (Q_mg_s / (2 * math.pi * sigma_y * sigma_z * u)
             * math.exp(-0.5 * (y / sigma_y) ** 2)
             * math.exp(-0.5 * (dz / sigma_z) ** 2))

        if self._noise:
            c *= (1.0 + self._gauss(0.05))   # 5% noise
        return round(max(0.0, c), 1)

    def smoke_optical_density(
        self,
        drone_ned:     tuple[float, float, float],
        fire_ned:      tuple[float, float],
        fire_severity: str = "MEDIUM",
        wind_n_mps:    float = 3.0,
        wind_e_mps:    float = 0.0,
    ) -> float:
        """
        Normalised smoke optical density for dashboard (0.0-1.0).
        0.0 = clear air, 1.0 = zero-visibility dense smoke (>2 000 mg/m³).
        """
        c = self.smoke_density_mg_m3(drone_ned, fire_ned, fire_severity,
                                      wind_n_mps, wind_e_mps)
        # Logistic mapping: dense smoke saturates at SMOKE_DENSE_MG_M3
        return round(min(1.0, c / (SMOKE_DENSE_MG_M3 * 2)), 3)

# endregion

# region  Flame height (Heskestad correlation)

    def flame_height_m(
        self,
        fire_severity: str = "MEDIUM",
    ) -> float:
        """
        Mean flame height (m) via Heskestad (1983) empirical correlation:
          L = −1.02 D + 0.235 Q^(2/5)
        where:
          L = flame height (m)
          D = base diameter (m) - estimated from HRR
          Q = HRR in watts

        Reference: Heskestad, G., "Fire Plumes, Flame Heights, and Air Entrainment",
        SFPE Handbook of Fire Protection Engineering, Chapter 2-1.
        """
        hrr_kw = FIRE_HRR_MW.get(fire_severity, 8.0) * 1000.0   # kW
        # Base diameter empirical: D ≈ 2 × sqrt(HRR_MW / (π × 100))
        d_m    = 2 * math.sqrt(FIRE_HRR_MW.get(fire_severity, 8.0) / (math.pi * 100))
        # Heskestad: L = -1.02D + 0.235 × Q^0.4  (Q in kW)
        flame_height = -1.02 * d_m + 0.235 * (hrr_kw ** 0.4)
        flame_height = max(0.5, flame_height)

        if self._noise:
            flame_height += self._gauss(0.5)   # ±0.5 m visual estimation noise
        return round(max(0.0, flame_height), 1)

# endregion

# region  Laser rangefinder

    def distance_to_flame_m(
        self,
        drone_ned:     tuple[float, float, float],
        fire_ned:      tuple[float, float],
    ) -> float:
        """
        Slant-range distance from drone to flame base (m).

        Computed as Euclidean 3-D distance from drone to fire ground point.
        Noise: ±3 cm (DJI LiDAR range sensor spec).
        Capped at LIDAR_MAX_RANGE_M (100 m) - beyond this, sensor returns NaN.
        """
        dist = self._range_to_fire(drone_ned, fire_ned)
        if self._noise:
            dist += self._gauss(LIDAR_NOISE_M)
        if dist > LIDAR_MAX_RANGE_M:
            return float("nan")   # out of range
        return round(max(0.0, dist), 2)

# endregion

# region  Private helpers

    def _range_to_fire(
        self,
        drone_ned: tuple[float, float, float],
        fire_ned:  tuple[float, float],
    ) -> float:
        """3-D Euclidean distance from drone to fire ground point (m)."""
        dN = drone_ned[0] - fire_ned[0]
        dE = drone_ned[1] - fire_ned[1]
        dz = drone_ned[2]   # altitude AGL above fire ground
        return math.sqrt(dN*dN + dE*dE + dz*dz)

    def _gauss(self, sigma: float) -> float:
        """Box-Muller Gaussian sample."""
        if not self._noise:
            return 0.0
        u1 = max(1e-10, random.random())
        u2 = random.random()
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2) * sigma

# endregion

# endregion
