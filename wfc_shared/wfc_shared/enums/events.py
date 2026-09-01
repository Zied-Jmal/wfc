"""
wfc_shared.enums.events
========================
All event type string constants used across the WFC system.
Categories
----------

  Command lifecycle    : what happened to a dispatched command
  Approval events      : operator gate decisions
  Tracker diagnostics  : internal consistency events
  Node lifecycle       : node registration / liveness
  Fire events          : fire detection and progression


"""

from __future__ import annotations

from typing import Final

# Command lifecycle
COMMAND_ISSUED: Final[str] = "COMMAND_ISSUED"
COMMAND_RECEIVED: Final[str] = "COMMAND_RECEIVED"
COMMAND_EXECUTED: Final[str] = "COMMAND_EXECUTED"
COMMAND_FAILED: Final[str] = "COMMAND_FAILED"

# Approval events
COMMAND_PENDING: Final[str] = "COMMAND_PENDING"
COMMAND_APPROVED: Final[str] = "COMMAND_APPROVED"
COMMAND_REJECTED: Final[str] = "COMMAND_REJECTED"

# Tracker diagnostics
INVALID_EVENT: Final[str] = "INVALID_EVENT"
INVALID_TRANSITION: Final[str] = "INVALID_TRANSITION"
DUPLICATE_EVENT: Final[str] = "DUPLICATE_EVENT"
DUPLICATE_STATE_EVENT: Final[str] = "DUPLICATE_STATE_EVENT"

# Node lifecycle
HEARTBEAT_RECEIVED: Final[str] = "HEARTBEAT_RECEIVED"
NODE_FAILED: Final[str] = "NODE_FAILED"
NODE_REGISTERED: Final[str] = "NODE_REGISTERED"
NODE_RECOVERED: Final[str] = "NODE_RECOVERED"

# Fire events
FIRE_DETECTED: Final[str] = "FIRE_DETECTED"  # sensor: initial detection
FIRE_SUPPRESSED: Final[str] = "FIRE_SUPPRESSED"  # fire being extinguished
FIRE_CONTAINED: Final[str] = "FIRE_CONTAINED"  # perimeter held
FIRE_INTENSITY_UPDATE: Final[str] = "FIRE_INTENSITY_UPDATE"  # scout: severity changed
FIRE_REKINDLED: Final[str] = "FIRE_REKINDLED"  # previously suppressed; re-ignited
FIRE_VERIFIED: Final[str] = "FIRE_VERIFIED"  # scout confirmed initial detection
FIRE_PERIMETER_UPDATE: Final[str] = "FIRE_PERIMETER_UPDATE"  # leader: perimeter estimate updated
