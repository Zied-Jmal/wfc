"""
action/resources.py - Drone Resource Model

Physics-based battery (Wh) and liquid payload (litres) model.
Replaces the abstract 0.0-1.0 fraction model with real-world quantities:
Battery:
- Capacity in watt-hours (Wh) = voltage (V) × capacity (Ah)
- Power consumption depends on flight phase:
Hover : ~800 W (thrust ≈ weight, η ≈ 60%)
Cruise : ~1 100 W (higher thrust + drag)
Max load : ~2 200 W (aggressive manoeuvres)
- Discharge model: P = V_nominal × I Wh consumed per dt
- Voltage sag under load (Peukert effect approximated linearly)
Payload (liquid - water or fire retardant):
- Tank capacity: 10 litres (firefighting drone) or 0 (scout)
- Flow rate: 0.5 L/s when pump active = 20 s for full tank
- Density: water = 1.000 kg/L, retardant = 1.050 kg/L
- Remaining mass affects drone weight affects power consumption
- Exposed as both litres and kg
Connectivity:
- RSSI model: distance-based with free-space path loss
- Fallback to battery-based model when distance unknown
ISO units:
energy : Wh (watt-hours)
power : W (watts)
capacity : Ah (ampere-hours)
voltage : V
mass : kg
volume : L (litres, 1 L = 1 dm³ = 0.001 m³)
flow rate : L/s
Reference drone: DJI Matrice 300 RTK + Zenmuse P1
Battery : 13.2 Ah × 44.4 V = 585.9 Wh
Max flight : ~55 min hover, ~40 min cruise with payload
"""

from __future__ import annotations
import math
import os
# region Drone Battery Specification
# DJI Matrice 300 RTK battery (TB60) - configurable via env
BATTERY_CAPACITY_WH = float(os.getenv("DRONE_BATTERY_CAPACITY_WH", "585.9"))
BATTERY_NOMINAL_VOLTAGE_V = 44.4 # V
BATTERY_CAPACITY_AH = 13.2 # Ah
# Power consumption by flight phase (W)
POWER_HOVER_W = 800.0 # W hovering in place
POWER_CRUISE_W = 1100.0 # W forward cruise at 14 m/s
POWER_MAX_W = 2200.0 # W aggressive manoeuvres / full throttle
POWER_SENSOR_W = 50.0 # W additional for sensors (camera, thermal, lidar)
POWER_PUMP_W = 120.0 # W additional for suppression pump
# Safe discharge thresholds (fraction of full capacity)
RTB_WARNING_WH_FRAC = 0.25 # 25% RTB warning
RTB_URGENT_WH_FRAC = 0.15 # 15% RTB urgent
BATTERY_CRITICAL_WH_FRAC = 0.05 # 5% critical / land now
# endregion
# region Liquid Payload Specification
TANK_CAPACITY_L = float(os.getenv("DRONE_TANK_CAPACITY_L", "10.0"))
PUMP_FLOW_RATE_L_S = float(os.getenv("DRONE_PUMP_FLOW_RATE_L_S", "0.5"))
WATER_DENSITY_KG_L = 1.000 # kg/L pure water
RETARDANT_DENSITY_KG_L = 1.050 # kg/L fire retardant (slightly denser than water)
LOW_PAYLOAD_THRESHOLD_L = float(os.getenv("DRONE_LOW_PAYLOAD_L", "1.5"))
# endregion
# region Connectivity model
# Free-space path loss for 900 MHz FHSS link (typical drone comms)
COMM_FREQ_MHZ = 900.0 # MHz
COMM_TX_POWER_DBM = 27.0 # dBm (0.5 W)
COMM_RX_SENSITIVITY_DBM = -95.0 # dBm minimum usable signal
COMM_MAX_RANGE_M = 35000.0 # m theoretical LOS range 900MHz/0.5W (WEAK ~12km, LOST ~37km)
class ConnectivityStatus:
    """RSSI-based link quality indicator constants."""
    STRONG = "STRONG" # RSSI > -80 dBm
    WEAK = "WEAK" # RSSI -80 to -90 dBm
    LOST = "LOST" # RSSI < -90 dBm

class RTBLevel:
    """Return-to-base urgency level constants."""
    NONE = "NONE"
    WARNING = "RTB_WARNING"
    URGENT = "RTB_URGENT"
    CRITICAL = "CRITICAL"
# endregion
# region DroneResourceModel
class DroneResourceModel:
    """Physics-based resource tracker.

    Battery modelled in Wh.  Payload modelled in litres.
    Call tick(dt, phase) every simulation step.

    Example:
        r = DroneResourceModel(payload_type="water")
        r.tick(dt=2.0, phase="CRUISE", pump_active=False)
        print(r.battery_wh)         # e.g. 583.6 Wh remaining
        print(r.payload_litres)     # e.g. 10.0 L
        print(r.battery_pct)        # e.g. 0.996
        print(r.payload_kg)         # e.g. 10.0 kg
        print(r.rtb_level)          # RTBLevel.NONE
    """

    def __init__(
        self,
        initial_battery_wh: float   = BATTERY_CAPACITY_WH,
        initial_payload_l:  float   = 0.0,              # 0 for scouts
        payload_type:       str     = "water",           # "water" | "retardant"
        base_station_dist_m: float  = 0.0,               # m  distance to GCS for RSSI
    ):
        """Initialize the drone resource model.

        Args:
            initial_battery_wh: Starting battery energy in watt-hours.
            initial_payload_l: Starting liquid payload in litres (0 for scouts).
            payload_type: "water" or "retardant" (affects density).
            base_station_dist_m: Distance to GCS in metres for RSSI calculation.
        """
        self._battery_wh      = float(initial_battery_wh)
        self._payload_l       = float(initial_payload_l)
        self._payload_density = (RETARDANT_DENSITY_KG_L if payload_type == "retardant"
                                 else WATER_DENSITY_KG_L)
        self._base_dist_m     = base_station_dist_m

# region  Battery properties

    @property
    def battery_wh(self) -> float:
        """Remaining battery energy (Wh)."""
        return round(max(0.0, self._battery_wh), 2)

    @property
    def battery_pct(self) -> float:
        """Remaining battery fraction 0.0-1.0."""
        return round(max(0.0, self._battery_wh / BATTERY_CAPACITY_WH), 4)

    @property
    def battery_voltage_v(self) -> float:
        """
        Estimated terminal voltage under load.
        Linear sag model: V = V_full − (V_full − V_empty) × (1 − SOC)
        V_full = 50.4 V (4.2 V/cell × 12S), V_empty = 39.6 V (3.3 V/cell × 12S)
        """
        soc      = self.battery_pct
        v_full   = 50.4
        v_empty  = 39.6
        return round(v_full - (v_full - v_empty) * (1.0 - soc), 2)

    @property
    def rtb_level(self) -> str:
        """Return-to-base urgency level based on battery state-of-charge.

        Returns:
            RTBLevel constant: NONE, WARNING, URGENT, or CRITICAL.
        """
        frac = self.battery_pct
        if frac <= BATTERY_CRITICAL_WH_FRAC:  return RTBLevel.CRITICAL
        if frac <= RTB_URGENT_WH_FRAC:         return RTBLevel.URGENT
        if frac <= RTB_WARNING_WH_FRAC:        return RTBLevel.WARNING
        return RTBLevel.NONE

    @property
    def should_return_to_base(self) -> bool:
        """True if battery is at or below urgent RTB threshold.

        Returns:
            True if the drone should return to base immediately.
        """
        return self.battery_pct <= RTB_URGENT_WH_FRAC

# endregion

# region  Payload properties

    @property
    def payload_litres(self) -> float:
        """Remaining liquid payload (L)."""
        return round(max(0.0, self._payload_l), 3)

    @property
    def payload_kg(self) -> float:
        """Remaining liquid payload mass (kg)."""
        return round(max(0.0, self._payload_l * self._payload_density), 3)

    @property
    def payload_pct(self) -> float:
        """Remaining payload fraction 0.0-1.0."""
        if TANK_CAPACITY_L == 0:
            return 0.0
        return round(max(0.0, self._payload_l / TANK_CAPACITY_L), 4)

    @property
    def low_payload(self) -> bool:
        """True when payload is below LOW_PAYLOAD_THRESHOLD_L."""
        return self._payload_l <= LOW_PAYLOAD_THRESHOLD_L

# endregion

# region  Connectivity

    def connectivity(self, distance_to_gcs_m: Optional[float] = None) -> str:
        """
        Estimate link quality based on free-space path loss (FSPL).

        FSPL (dB) = 20 log10(d) + 20 log10(f) + 20 log10(4π/c)
        At 900 MHz: FSPL ≈ 20 log10(d) + 20 log10(900) - 147.55

        Returns ConnectivityStatus.STRONG / WEAK / LOST
        """
        d = distance_to_gcs_m if distance_to_gcs_m is not None else self._base_dist_m
        d = max(1.0, d)

        fspl_db  = (20 * math.log10(d)
                    + 20 * math.log10(COMM_FREQ_MHZ * 1e6)
                    - 147.55)   # dB
        rssi_dbm = COMM_TX_POWER_DBM - fspl_db
        # Add 6 dB for antenna gains (transmit + receive)
        rssi_dbm += 6.0

        if rssi_dbm > -80.0:
            return ConnectivityStatus.STRONG
        if rssi_dbm > -90.0:
            return ConnectivityStatus.WEAK
        return ConnectivityStatus.LOST

# endregion

# region  Tick

    def tick(
        self,
        dt:           float,
        phase:        str   = "HOVER",   # HOVER | CRUISE | SUPPRESS | MAX
        pump_active:  bool  = False,
        sensors_on:   bool  = True,
    ) -> None:
        """
        Advance resource model by dt seconds.

        Args:
            dt          : time step (s)
            phase       : flight phase - determines base power draw
            pump_active : True when suppression pump is running
            sensors_on  : True when thermal/optical sensors are powered

        Battery drain:
            Wh_consumed = (P_flight + P_sensors + P_pump) × dt / 3600

        Payload drain:
            dV = PUMP_FLOW_RATE_L_S × dt   when pump_active and payload > 0
        """
# Base power by phase (W)
        phase_power = {
            "HOVER":    POWER_HOVER_W,
            "CRUISE":   POWER_CRUISE_W,
            "SUPPRESS": POWER_CRUISE_W,   # flying slowly over fire
            "MAX":      POWER_MAX_W,
        }.get(phase.upper(), POWER_HOVER_W)

# Additional consumers
        sensor_power  = POWER_SENSOR_W if sensors_on else 0.0
        pump_power    = POWER_PUMP_W   if pump_active else 0.0

        total_power_w = phase_power + sensor_power + pump_power

# Convert W × s Wh (divide by 3600 s/h)
        wh_consumed   = total_power_w * dt / 3600.0
        self._battery_wh = max(0.0, self._battery_wh - wh_consumed)

# Payload drain (litres)
        if pump_active and self._payload_l > 0.0:
            vol_dropped      = PUMP_FLOW_RATE_L_S * dt
            self._payload_l  = max(0.0, self._payload_l - vol_dropped)

# endregion

# region  Operations

    def refuel(self, wh: Optional[float] = None) -> None:
        """
        Recharge battery at base station.
        Full recharge if wh not specified.
        """
        self._battery_wh = wh if wh is not None else BATTERY_CAPACITY_WH

    def reload_payload(self, litres: Optional[float] = None) -> None:
        """Reload tank at base. Full reload if litres not specified."""
        self._payload_l = litres if litres is not None else TANK_CAPACITY_L

    def service_at_base(self) -> None:
        """Full refuel + reload (simulates landing at base station)."""
        self.refuel()
        self.reload_payload()

# endregion


# Optional type import for connectivity signature
try:
    from typing import Optional
except ImportError:
    pass

# endregion
