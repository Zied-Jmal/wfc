"""
wfc_shared.schemas.telemetry - Telemetry schemas (ISO 80000 units throughout).
Schema hierarchy
----------------
DroneTelemetry : drone leader (topic: wfc/telemetry/{drone_id})
SwarmStatusSnapshot : leader commander (topic: wfc/swarm/status/{leader_id})
FireIntensityUpdate : leader/scout commander (topic: wfc/events/fire/intensity)
ISO unit conventions (ISO 80000)
---------------------------------
Position : WGS-84 decimal degrees (lat_deg, lon_deg)
Altitude : metres above MSL [m AMSL]
Energy : watt-hours [Wh]
Fraction : dimensionless [0.0 - 1.0]
Volume : litres [L]
Mass : kilograms [kg]
Temperature: degrees Celsius [C]
Distance : metres [m]
Speed : metres per second [m/s]
Direction : degrees true met. FROM [T]
Concentration: milligrams per cubic m [mg/m3]
Time : UNIX epoch seconds UTC [s]
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

# Constrained type aliases
IntensityLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
"""Fire intensity rating from scout thermal reading."""
TaskType = Literal["SCOUTING", "SUPPRESSING", "RETURNING", "IDLE"]
"""Operational task assigned to a drone."""
ConnectivityLevel = Literal["STRONG", "WEAK", "LOST"]
"""RSSI-based link quality indicator."""
SpreadRate = Literal["SLOW", "MODERATE", "RAPID"]
"""Fire spread rate derived from centroid drift."""
SwarmStatus = Literal["ENGAGING", "CONTAINING", "SUPPRESSING", "WITHDRAWING", "IDLE", "SCOUTING"]
"""Aggregated swarm status from TelemetryAggregator."""


# DroneTelemetry

class DroneTelemetry(BaseModel):
    """Raw telemetry published by a drone to its leader every 2 s.

    Topic : wfc/telemetry/{drone_id}  |  QoS 0 (loss-tolerant)

    Field groups
    ------------
    Identity    - who is sending and to whom
    Position    - WGS-84 GPS + altitude
    Battery     - both absolute (Wh) and fractional (pct)
    Payload     - liquid suppressant (firefighters only; scouts = 0)
    Task        - current operational mode + connectivity quality
    Thermal     - FLIR Lepton 3.5 output (scouts only)
    Smoke       - optical / particulate sensors (scouts only)
    Visual      - visual + laser rangefinder (scouts only)
    Atmospheric - anemometer readings (scouts only)
    Suppression - drop metrics (firefighters only)
    """

    # Identity
    drone_id: str
    """Unique node_id of this drone."""
    leader_id: str
    """Node_id of this drone's current parent leader."""
    timestamp: float
    """UNIX epoch seconds (UTC)."""

    # Position (WGS-84)
    position: tuple[float, float]
    """(lat_deg, lon_deg) WGS-84 decimal degrees."""
    altitude_m_amsl: float = 0.0
    """Metres above mean sea level [m AMSL]."""

    # Battery (ISO)
    battery_wh: float = 0.0
    """Watt-hours remaining (DJI TB60 max = 585.9 Wh) [Wh]."""
    battery_pct: float = 0.0
    """State-of-charge fraction [0.0 - 1.0]."""

    # Payload - firefighters only (scouts always 0.0)
    payload_litres: float = 0.0
    """Liquid suppressant remaining [L]."""
    payload_kg: float = 0.0
    """Suppressant mass remaining [kg]."""

    # Task / connectivity
    task: TaskType = "IDLE"
    """Operational mode: SCOUTING | SUPPRESSING | RETURNING | IDLE."""
    connectivity: ConnectivityLevel = "STRONG"
    """RSSI-based link quality: STRONG | WEAK | LOST."""

    # Thermal camera - scouts only (FLIR Lepton 3.5)
    thermal_peak_temp_c: float | None = None
    """Hottest pixel in FLIR frame (C, Stefan-Boltzmann model)."""
    thermal_coverage_pct: float | None = None
    """Fraction of FLIR FOV above 300 C threshold [0.0 - 1.0]."""

    # Smoke sensors - scouts only
    smoke_density_mg_m3: float | None = None
    """Particulate concentration (mg/m3, Gaussian plume model)."""
    smoke_optical_density: float | None = None
    """Normalised optical density [0.0 - 1.0] (dashboard display)."""

    # Visual / ranging - scouts only
    flame_height_m: float | None = None
    """Estimated flame height (m, Heskestad correlation)."""
    distance_to_flame_m: float | None = None
    """Slant-range to nearest flame front (m, laser rangefinder)."""
    perimeter_estimate_m: float | None = None
    """Estimated fire perimeter (m, updated each orbit pass)."""

    # Atmospheric - scouts only
    wind_speed_mps: float | None = None
    """Wind speed (m/s, anemometer)."""
    wind_direction_deg: float | None = None
    """Wind FROM direction - met. convention (T)."""

    # Suppression metrics - firefighters only
    litres_delivered: float | None = None
    """Total suppressant dropped on fire [L]."""
    suppression_effectiveness_pct: float | None = None
    """Estimated suppression fraction [0.0 - 1.0]."""
    drop_passes: int | None = None
    """Integer count of completed drop passes."""
    pump_active: bool | None = None
    """True while suppression pump is running."""


# SwarmStatusSnapshot

class SwarmStatusSnapshot(BaseModel):
    """Aggregated telemetry published by a leader to the commander every 10 s.

    Topic : wfc/swarm/status/{leader_id}  |  QoS 1 (guaranteed delivery)

    The commander sees ONLY this - never raw DroneTelemetry.
    Values are aggregated across all drones by TelemetryAggregator.
    """

    leader_id: str
    """Node_id of the publishing leader."""
    fire_id: str | None = None
    """Fire currently being fought, if any."""
    timestamp: float
    """UNIX epoch seconds (UTC)."""

    # Fleet counts
    active_drones: int = 0
    """Drones with recent telemetry."""
    lost_drones: int = 0
    """Drones declared LOST."""

    # Aggregated battery (ISO)
    avg_battery_pct: float = 0.0
    """Fleet-mean state-of-charge [0.0 - 1.0]."""
    min_battery_wh: float = 0.0
    """Lowest single-drone energy remaining [Wh]."""

    # Aggregated payload (ISO)
    avg_payload_litres: float = 0.0
    """Fleet-mean suppressant remaining [L]."""
    total_litres_delivered: float = 0.0
    """Sum of all drops across every fighter [L]."""

    # Fire situation
    perimeter_estimate_m: float | None = None
    """Fire perimeter estimate [m]."""
    suppression_pct: float | None = None
    """Estimated suppression fraction [0.0 - 1.0]."""
    fire_intensity: IntensityLevel = "LOW"
    """LOW | MEDIUM | HIGH | CRITICAL."""
    status: SwarmStatus = "IDLE"
    """ENGAGING | CONTAINING | SUPPRESSING | WITHDRAWING | IDLE | SCOUTING."""
    spread_rate: SpreadRate | None = None
    """SLOW | MODERATE | RAPID."""

    # Atmospheric (from scout telemetry)
    wind_speed_mps: float | None = None  # [m/s]
    wind_direction_deg: float | None = None  # [T]


# FireIntensityUpdate

class FireIntensityUpdate(BaseModel):
    """Published when a leader/scout detects a fire intensity change.

    Topic : wfc/events/fire/intensity  |  QoS 1

    Triggers SeverityIncreaseRule / FireExpansionRule in the commander's
    rule engine, which may produce a REINFORCING mission transition or
    an ESCALATE_FIRE approval request.
    """

    fire_id: str
    """Fire being reported on."""
    leader_id: str
    """Reporting leader's node_id."""
    timestamp: float
    """UNIX epoch seconds (UTC)."""
    new_intensity: IntensityLevel
    """Updated fire intensity: LOW | MEDIUM | HIGH | CRITICAL."""
    perimeter_m: float | None = None
    """Updated perimeter estimate [m]."""
    spread_rate: SpreadRate | None = None
    """SLOW | MODERATE | RAPID."""
    wind_speed_mps: float | None = None
    """Wind speed [m/s] from scout at time of update."""


# Module-level constants
MAX_BATTERY_WH: Final[float] = 585.9
"""Maximum battery energy for DJI TB60 (Wh)."""
TANK_CAPACITY_L: Final[float] = 100.0
"""Maximum suppressant tank capacity (L)."""
TELEMETRY_PUBLISH_INTERVAL_S: Final[float] = 2.0
"""Drone telemetry publish interval (s)."""
SNAPSHOT_PUBLISH_INTERVAL_S: Final[float] = 10.0
"""Leader snapshot publish interval (s)."""
