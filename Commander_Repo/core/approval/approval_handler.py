# Processes operator decisions arriving on
# wfc/approval/response and delegates to PendingCommandStore.
"""approval_handler.py
ApprovalHandler - operator decision processor
- Translate raw MQTT approval payloads into store calls
- Delegate approve / reject to PendingCommandStore
- Log and drop malformed or unknown decision messages
Expected payload:
{
"pending_id": "<uuid>",
"decision": "APPROVED" | "REJECTED",
"operator_id": "<hmi-id>", # optional
"reason": "<text>", # optional, REJECTED only
}
"""

from __future__ import annotations

from typing import Any

from core.approval.pending_store import PendingCommandStore
from core.utils.logger import log

# Standard Library
# Third-Party Libraries
# Project Imports
from wfc_shared.enums.approval import APPROVED, REJECTED

# region  CLASS - ApprovalHandler


class ApprovalHandler:
    """
    Translates raw MQTT payloads from wfc/approval/response into
    store.approve() / store.reject() calls.
    """

    # region  INITIALISATION

    def __init__(self, store: PendingCommandStore) -> None:
        self._store = store

    # endregion

    # region  PUBLIC API

    def handle(self, payload: dict[str, Any]) -> None:
        """Process one approval response message."""
        pending_id = payload.get("pending_id")
        decision = payload.get("decision")
        operator_id = payload.get("operator_id")
        reason = payload.get("reason", "operator_rejected")

        if not pending_id:
            log("ApprovalHandler", "missing pending_id - dropped", channel="APPROVAL")
            return

        if decision == APPROVED:
            self._store.approve(pending_id, operator_id=operator_id)

        elif decision == REJECTED:
            self._store.reject(pending_id, reason=reason, operator_id=operator_id)

        else:
            log(
                "ApprovalHandler",
                f"unknown decision='{decision}' for pending_id={pending_id[:8]} - dropped",
                channel="APPROVAL",
            )

    # endregion


# endregion (end of class ApprovalHandler)
