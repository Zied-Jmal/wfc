"""
wfc_shared.schemas.domain_event - Commander decision / state-change fact.
DomainEvent records are written to DomainEventLog (SQLite).
These are internal records of what the COMMANDER decided and why.
They are distinct from FireEvent (external sensor input).
Used by: DomainEventLog, CentralNode, BackupCommander (snapshot sync).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Constrained type aliases
DomainEventType = Literal[
    "FIRE_DETECTED",
    "FIRE_CONTAINED",
    "FIRE_SUPPRESSED",
    "FIRE_DISPATCHED",
    "FIRE_REDISPATCHED",
    "LEADER_DIED",
    "ESCALATION_REQUESTED",
    "ESCALATION_APPROVED",
    "ESCALATION_REJECTED",
    "COMMAND_ACK_RECEIVED",
    "COMMAND_ACK_EXECUTED",
    "COMMAND_ACK_FAILED",
    "NODE_BECAME_AVAILABLE",
    "LEADER_REPLACED",
]
"""Internal domain event types from wfc_shared.enums.domain_event_types."""


class DomainEvent(BaseModel):
    """Commander decision or state-change fact stored in DomainEventLog.

    Args:
        event_id: UUID - globally unique; used for dedup on backup sync.
        event_type: One of the DomainEventType constants.
        fire_id: The fire this event concerns (None for node-only events).
        node_id: The node this event concerns (None for fire-only events).
        reason: Free-text context written at the call site.
        payload: Type-specific extra data (strategy, severity, election info, ...).
        sequence: Assigned by DomainEventRepository on INSERT (auto-increment PK).
        timestamp: UNIX epoch seconds (UTC).
        source: Which commander wrote this ("commander" by default).
        replayed: True when replayed from storage on startup.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """UUID - globally unique; used for dedup on backup sync."""
    event_type: DomainEventType
    """One of the DomainEventType constants."""
    fire_id: str | None = None
    """The fire this event concerns (None for node-only events)."""
    node_id: str | None = None
    """The node this event concerns (None for fire-only events)."""
    reason: str | None = None
    """Free-text context written at the call site."""
    payload: dict[str, Any] = Field(default_factory=dict)
    """Type-specific extra data (strategy, severity, election info, ...)."""
    sequence: int | None = None
    """Assigned by DomainEventRepository on INSERT (auto-increment PK)."""
    timestamp: float = Field(default_factory=time.time)
    """UNIX epoch seconds (UTC)."""
    source: str = "commander"
    """Which commander wrote this ("commander" by default)."""
    replayed: bool = False
    """True when replayed from storage on startup."""
