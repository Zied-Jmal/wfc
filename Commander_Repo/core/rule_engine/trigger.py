from __future__ import annotations

from enum import StrEnum


class EvalTrigger(StrEnum):
    NEW_FIRE = "new_fire"
    REDISPATCH = "redispatch"
    NODE_AVAILABLE = "node_available"
    INTENSITY_UPDATE = "intensity_update"
    TELEMETRY_UPDATE = "telemetry_update"
    REKINDLED = "rekindled"
    ELECTION_RESULT = "election_result"
    ACK_TIMEOUT = "ack_timeout"
    MANUAL = "manual"
