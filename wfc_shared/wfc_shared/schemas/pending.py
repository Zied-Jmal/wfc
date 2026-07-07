"""
wfc_shared.schemas.pending - Command held in the approval gate.
PendingCommand is a command awaiting an operator decision.
Used by: PendingCommandStore, ApprovalGate, ApprovalHandler, Dashboard.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from wfc_shared.schemas.commands import Command

# Constrained type aliases
ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"]
"""Approval decision status from wfc_shared.enums.approval."""


class PendingCommand(BaseModel):
    """Command envelope awaiting operator approval.

    Args:
        pending_id: UUID unique record identifier.
        command: The Command envelope awaiting approval.
        status: PENDING | APPROVED | REJECTED | EXPIRED.
        created_at: UNIX epoch when the command entered the gate.
        expires_at: UNIX epoch TTL (None = no expiry).
        decided_at: UNIX epoch when operator made a decision (None if pending).
        operator_id: Node_id of the operator who decided (None if pending).
        reason: Free-text rejection / expiry reason.
    """

    pending_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """UUID unique record identifier."""
    command: Command
    """The Command envelope awaiting approval."""
    status: ApprovalStatus = "PENDING"
    """PENDING | APPROVED | REJECTED | EXPIRED."""
    created_at: float = Field(default_factory=time.time)
    """UNIX epoch when the command entered the gate."""
    expires_at: float | None = None
    """UNIX epoch TTL (None = no expiry)."""
    decided_at: float | None = None
    """UNIX epoch when operator made a decision (None if pending)."""
    operator_id: str | None = None
    """Node_id of the operator who decided (None if pending)."""
    reason: str | None = None
    """Free-text rejection / expiry reason."""
