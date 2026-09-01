# Canonical transition table; terminal states explicit.
# STATUS_TO_EVENT_TYPE added: the canonical mapping from
# the short "status" strings field nodes log/display
# (RECEIVED/EXECUTED/FAILED) to the COMMAND_* event_type
# strings EVENT_TO_STATE actually recognizes. This used
# to be duplicated (or just missing) at each call site -
# defined once here so FieldNode and CommanderCore can't
"""lifecycle_rules.py
Command lifecycle state machine constants
- Define valid state transitions for command lifecycle
- Declare terminal states
- Map event type strings to target states
# region  STATE MACHINE TABLES
"""

from __future__ import annotations

VALID_TRANSITIONS: dict[str, set[str]] = {
    "ISSUED": {"RECEIVED"},
    "RECEIVED": {"EXECUTED", "FAILED"},
    "EXECUTED": set(),  # terminal
    "FAILED": set(),  # terminal
}

TERMINAL_STATES: set[str] = {"EXECUTED", "FAILED"}

EVENT_TO_STATE: dict[str, str] = {
    "COMMAND_ISSUED": "ISSUED",
    "COMMAND_RECEIVED": "RECEIVED",
    "COMMAND_EXECUTED": "EXECUTED",
    "COMMAND_FAILED": "FAILED",
}

# Short status string (as used in ACK payloads, logs, dashboard)
# COMMAND_* event_type (as required by EVENT_TO_STATE above).
STATUS_TO_EVENT_TYPE: dict[str, str] = {
    "RECEIVED": "COMMAND_RECEIVED",
    "EXECUTED": "COMMAND_EXECUTED",
    "FAILED": "COMMAND_FAILED",
}

# endregion
