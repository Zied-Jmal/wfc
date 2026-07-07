# get_node_zone() and get_node_location() added.
# Config class values made lazy (read at call time, not
# import time) so tests that set env vars after import
"""config.py
Config - environment variable loader and accessors
- Load the correct .env file based on ENV
- Expose typed accessors for MQTT and node settings
- Parse NODE_LOCATION from "x,y" string to float tuple
"""

from __future__ import annotations

import os
from typing import Final

from dotenv import load_dotenv

# region  VERSION

WFC_VERSION: Final = "1.7"

# endregion

# region  BOOT

ENV = os.getenv("ENV", "dev")

def _load_env_file() -> None:
    mapping = {
        "test":   ".env.test",
        "prod":   ".env.prod",
        "docker": ".env.docker",
    }
    load_dotenv(mapping.get(ENV, ".env"), override=False)

_load_env_file()

# endregion

# region  ACCESSORS
# All accessors call os.getenv() at invocation time - NOT at
# import time. This means:
# - Tests can set os.environ values before calling these
# functions and always get correct results.
# - monkeypatch / pytest fixtures work without import order
# constraints.
# - The old `Config.MQTT_HOST = os.getenv(...)` class-level
# pattern (evaluated once on import) is gone.

def get_mqtt_host() -> str:
    return os.getenv("MQTT_HOST", "localhost") or "localhost"

def get_mqtt_port() -> int:
    return int(os.getenv("MQTT_PORT", "1883") or 1883)

def get_node_id() -> str:
    return os.getenv("NODE_ID", "central-commander")

def get_node_type() -> str:
    return os.getenv("NODE_TYPE", "CENTRAL_COMMANDER")

def get_node_zone() -> str | None:
    """Return the node's zone label, or None if not configured."""
    return os.getenv("NODE_ZONE", "") or None

def get_node_location() -> tuple[float, float] | None:
    """Parse NODE_LOCATION env var ("x,y") into a float tuple.

    Returns None if not set or invalid.
    """
    raw = (os.getenv("NODE_LOCATION", "") or "").strip()
    if not raw:
        return None
    try:
        parts = raw.split(",")
        if len(parts) != 2:
            return None
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None

def get_db_path() -> str:
    """Return the SQLite database path for this node.

    Resolution order:
      1. WFC_DB_PATH env var, if set to a non-empty string
      2. data/<NODE_ID>.db  - keeps each node's storage isolated
         so multiple nodes running on the same host never share
         a database file.
    """
    explicit = os.getenv("WFC_DB_PATH", "").strip()
    if explicit:
        return explicit
    return f"data/{get_node_id()}.db"

# endregion
