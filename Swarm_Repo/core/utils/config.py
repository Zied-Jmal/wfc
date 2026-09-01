# core/utils/config.py
from __future__ import annotations

import os
from typing import Any, Final

from dotenv import load_dotenv

SWARM_VERSION: Final[str] = "3.0.0"
ENV: Final[str] = os.getenv("ENV", "dev")


def _load_env_file() -> None:
    """Load environment file based on ENV setting."""

    mapping = {
        "test": ".env.test",
        "prod": ".env.prod",
        "docker": ".env.docker",
    }
    load_dotenv(mapping.get(ENV, ".env"), override=False)


_load_env_file()


# MQTT


def get_mqtt_host() -> str:
    """Return MQTT broker hostname from MQTT_HOST env var (default: localhost)."""
    return os.getenv("MQTT_HOST", "localhost") or "localhost"


def get_mqtt_port() -> int:
    """Return MQTT broker port from MQTT_PORT env var (default: 1883)."""
    return int(os.getenv("MQTT_PORT", "1883") or 1883)


# Node identity


def get_node_id() -> str:
    """Return node id from NODE_ID env var (default: swarm-node-01)."""
    return os.getenv("NODE_ID", "swarm-node-01")


def get_node_zone() -> str | None:
    """Return node zone from NODE_ZONE env var, or None if unset/empty."""
    return os.getenv("NODE_ZONE", "") or None


def get_node_location() -> tuple[float, float] | None:
    """
    Parse NODE_LOCATION env var as (lat_deg, lon_deg).
    Format: "lat,lon"  e.g. "36.8065,10.1815"
    Returns None if not set or malformed.
    """
    raw = (os.getenv("NODE_LOCATION", "") or "").strip()
    if not raw:
        return None
    try:
        parts = raw.split(",")
        if len(parts) < 2:
            return None
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None


def get_home_alt_m() -> float:
    """
    Return home altitude above mean sea level (m AMSL).

    HOME_ALT_M env var - home altitude above mean sea level (m AMSL).
    Default: 50.0 m (suitable for low-lying terrain).
    Set to actual elevation of the deployment site.
    """
    return float(os.getenv("HOME_ALT_M", "50.0") or 50.0)


def get_node_gps() -> Any:  # pyright: ignore[reportUnknownParameterType]
    """
    Build a GPSCoord from NODE_LOCATION + HOME_ALT_M.
    Returns GPSCoord(lat, lon, alt_m) or a safe default (Tunis area).

    Import is deferred to avoid circular imports at module load time.
    """
    from action.gps import GPSCoord

    loc = get_node_location()
    alt = get_home_alt_m()
    if loc is None:
        return GPSCoord(36.8065, 10.1815, alt)
    return GPSCoord(loc[0], loc[1], alt)


# Wind model initialisation


def get_wind_speed_mps() -> float:
    """
    Return initial mean wind speed (m/s) from WIND_SPEED_MPS env var.

    The WindModel random-walks from this value.
    Default: 5.0 m/s (light breeze, Beaufort 3).
    """
    return float(os.getenv("WIND_SPEED_MPS", "5.0") or 5.0)


def get_wind_dir_deg() -> float:
    """
    Return initial wind FROM direction, degrees true (°T).

    Met convention: 225 = SW wind (blowing FROM the SW, toward NE).
    Default: 225.0 (SW, common Mediterranean afternoon wind).
    """
    return float(os.getenv("WIND_DIR_DEG", "225.0") or 225.0)


def get_turbulence() -> str:
    """
    Return Dryden turbulence severity from TURBULENCE env var.

    Values: NONE | LIGHT | MODERATE | SEVERE
    Default: LIGHT (σ = 0.5 m/s, typical calm open terrain).
    """
    val = (os.getenv("TURBULENCE", "LIGHT") or "LIGHT").upper().strip()
    if val not in ("NONE", "LIGHT", "MODERATE", "SEVERE"):
        return "LIGHT"
    return val


# Drone resource initialisation


def get_payload_type() -> str:
    """
    Return liquid suppressant type from PAYLOAD_TYPE env var.

    Values: "water" | "retardant"
    Default: "water"
    Retardant is slightly denser (1.050 kg/L vs 1.000 kg/L).
    """
    val = (os.getenv("PAYLOAD_TYPE", "water") or "water").lower().strip()
    return val if val in ("water", "retardant") else "water"


def get_initial_payload_l() -> float:
    """
    Return starting payload in litres from INITIAL_PAYLOAD_L env var.

    Default: 10.0 L (full tank, TANK_CAPACITY_L).
    Set lower to simulate a partially-loaded drone at startup.
    """
    return float(os.getenv("INITIAL_PAYLOAD_L", "10.0") or 10.0)


def get_initial_battery_wh() -> float:
    """
    Return starting battery in watt-hours from INITIAL_BATTERY_WH env var.

    Default: 585.9 Wh (DJI TB60 full charge).
    Set lower to simulate a partially-charged drone at startup.
    """
    return float(os.getenv("INITIAL_BATTERY_WH", "585.9") or 585.9)


# Swarm-specific (unchanged from V1)


def get_leader_id() -> str:
    """Return leader node id from LEADER_ID env var (default: sl-A-01)."""
    return os.getenv("LEADER_ID", "sl-A-01")


def get_backup_peers() -> list[str]:
    """Return list of backup peer node ids from BACKUP_PEERS env var (comma-separated)."""
    raw = os.getenv("BACKUP_PEERS", "")
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_is_backup() -> bool:
    """Return whether this node is a backup from IS_BACKUP env var (default: false)."""
    return os.getenv("IS_BACKUP", "false").lower() in ("1", "true", "yes")


def get_swarm_status_interval() -> float:
    """Return swarm status interval in seconds (default: 10)."""
    return float(os.getenv("SWARM_STATUS_INTERVAL", "10"))


def get_leader_heartbeat_timeout() -> float:
    """Return leader heartbeat timeout in seconds (default: 10)."""
    return float(os.getenv("LEADER_HEARTBEAT_TIMEOUT", "10"))


def get_election_timeout() -> float:
    """Return election timeout in seconds (default: 5)."""
    return float(os.getenv("ELECTION_TIMEOUT", "5"))


# Suppression flight parameters (firefighting drone)


def get_drop_altitude_m() -> float:
    """Return suppression drop altitude in metres (default: 20.0)."""
    return float(os.getenv("SUPPRESS_DROP_ALTITUDE_M", "20.0") or 20.0)


def get_approach_speed_mps() -> float:
    """Return suppression approach speed in m/s (default: 6.0)."""
    return float(os.getenv("SUPPRESS_APPROACH_SPEED_MPS", "6.0") or 6.0)


def get_transit_speed_mps() -> float:
    """Return suppression transit speed in m/s (default: 14.0)."""
    return float(os.getenv("SUPPRESS_TRANSIT_SPEED_MPS", "14.0") or 14.0)


def get_egress_speed_mps() -> float:
    """Return suppression egress speed in m/s (default: 14.0)."""
    return float(os.getenv("SUPPRESS_EGRESS_SPEED_MPS", "14.0") or 14.0)


def get_approach_distance_m() -> float:
    """Return suppression approach distance in metres (default: 120.0)."""
    return float(os.getenv("SUPPRESS_APPROACH_DISTANCE_M", "120.0") or 120.0)


def get_egress_distance_m() -> float:
    """Return suppression egress distance in metres (default: 100.0)."""
    return float(os.getenv("SUPPRESS_EGRESS_DISTANCE_M", "100.0") or 100.0)


def get_tank_capacity_l() -> float:
    """Return drone tank capacity in litres (default: 10.0)."""
    return float(os.getenv("DRONE_TANK_CAPACITY_L", "10.0") or 10.0)


def get_pump_flow_rate_l_s() -> float:
    """Return drone pump flow rate in L/s (default: 0.5)."""
    return float(os.getenv("DRONE_PUMP_FLOW_RATE_L_S", "0.5") or 0.5)


def get_low_payload_threshold_l() -> float:
    """Return low-payload warning threshold in litres (default: 1.5)."""
    return float(os.getenv("DRONE_LOW_PAYLOAD_L", "1.5") or 1.5)


def get_battery_capacity_wh() -> float:
    """Return drone battery capacity in Wh (default: 585.9)."""
    return float(os.getenv("DRONE_BATTERY_CAPACITY_WH", "585.9") or 585.9)
