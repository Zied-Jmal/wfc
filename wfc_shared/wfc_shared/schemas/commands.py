"""
wfc_shared.schemas.commands - Command envelope for commander/leader to field node.
Topic : wfc/command/{node_id}  |  QoS 1 (guaranteed; commands must not be silently dropped)
trace_id lifecycle
------------------
A placeholder UUID is generated at construction time so the field is
never None.  CommandDispatcher.send() overwrites it with the CANONICAL
trace_id before publishing.

- Deterministic for idempotent command types (RESPOND_TO_FIRE,
  CONTAIN_FIRE, STAND_DOWN) - keyed by (command_type, fire_id,
  target_node) so duplicate dispatches from Central + Backup collapse.
- Random UUID4 for all other command types.

The value stored in CommandTracker and sent over the wire are the same
dispatcher-calculated value.  The constructor placeholder is never sent.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Constrained type aliases
CommandType = Literal[
    "RESPOND_TO_FIRE",
    "CONTAIN_FIRE",
    "STAND_DOWN",
    "REINFORCE_FIRE",
    "ABORT_MISSION",
    "REASSIGN_LEADER",
    "CONFIRM_LEADERSHIP",
    "DISPATCH_DRONE",
    "RECALL_DRONE",
    "UPDATE_TASK",
    "ESCALATE_FIRE",
]
"""Valid command types from wfc_shared.enums.command_types."""


class Command(BaseModel):
    """Outgoing command from commander or leader to a field node.

    Args:
        command_id: UUID unique per command instance.
        trace_id: UUID for distributed tracking; overwritten by CommandDispatcher.
        target_node: Node_id of the intended recipient.
        command_type: One of the CommandType constants.
        payload: Command-specific data dict (see command contract table).
        timestamp: UNIX epoch seconds (UTC) at creation.
    """

    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """UUID unique per command instance."""
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """UUID for distributed tracking; overwritten by CommandDispatcher."""
    target_node: str
    """Node_id of the intended recipient."""
    command_type: CommandType
    """One of the CommandType constants."""
    payload: dict[str, Any] = Field(default_factory=dict)
    """Command-specific data dict (see command contract table)."""
    timestamp: float = Field(default_factory=time.time)
    """UNIX epoch seconds (UTC) at creation."""
