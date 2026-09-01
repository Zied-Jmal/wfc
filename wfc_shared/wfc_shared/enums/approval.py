"""
wfc_shared.enums.approval
==========================
Approval decision status constants.
Used by PendingCommandStore and ApprovalHandler.
"""

from __future__ import annotations

from typing import Final

PENDING: Final[str] = "PENDING"  # awaiting operator decision
APPROVED: Final[str] = "APPROVED"  # operator approved; command will be dispatched
REJECTED: Final[str] = "REJECTED"  # operator rejected; command discarded
EXPIRED: Final[str] = "EXPIRED"  # TTL elapsed before decision
