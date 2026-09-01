# Pre-dispatch router. Commands flagged requires_approval
# are held in PendingCommandStore; others go straight
"""approval_gate.py
ApprovalGate - pre-dispatch command router
- Route commands to PendingCommandStore when approval
is required, or directly to CommandDispatcher
- Return trace_id on direct dispatch
- Return pending_id on held commands
"""

from __future__ import annotations

# Standard Library
import time
from typing import Final

from core.approval.pending_store import PendingCommandStore
from core.commands_monitor.command_dispatcher import CommandDispatcher
from core.utils.logger import log

# Third-Party Libraries
# Project Imports
from wfc_shared.schemas.commands import Command
from wfc_shared.schemas.pending import PendingCommand

DEFAULT_TTL: Final = 30.0

# region  CLASS - ApprovalGate


class ApprovalGate:
    """Sits between RuleEngine and CommandDispatcher.
    submit(command, requires_approval=False) immediate dispatch
    submit(command, requires_approval=True) held in PendingCommandStore
    """

    # region  INITIALISATION

    def __init__(self, dispatcher: CommandDispatcher, store: PendingCommandStore) -> None:
        self._dispatcher = dispatcher
        self._store = store

    # endregion

    # region  PUBLIC API

    def submit(self, command: Command, requires_approval: bool = False) -> str | None:
        """
        Route the command.

        Returns:
            trace_id   (str) if dispatched immediately
            pending_id (str) if held for approval
        """
        if not requires_approval:
            trace_id = self._dispatcher.send(command)
            log(
                "ApprovalGate",
                f"direct dispatch cmd={command.command_type} target={command.target_node}",
                channel="APPROVAL",
            )
            return trace_id

        pending = PendingCommand(
            command=command,
            created_at=time.time(),
        )
        self._store.add(pending)
        log(
            "ApprovalGate",
            f"held for approval cmd={command.command_type} "
            f"target={command.target_node} pending_id={pending.pending_id[:8]}",
            channel="APPROVAL",
        )
        return pending.pending_id

    # endregion


# endregion (end of class ApprovalGate)
