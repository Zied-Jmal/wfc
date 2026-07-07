"""command_tracker.py
CommandTracker - deterministic command lifecycle tracker
- Per-command lifecycle tracking
- event_id deduplication (global, cross-trace)
- Strict state machine enforcement
- Full audit history
Does NOT:
- Communicate via MQTT
- Generate or retry events
- Interpret business payload
"""

from __future__ import annotations

import time
from typing import Any

from core.commands_monitor.lifecycle_rules import (
    EVENT_TO_STATE,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
)
from core.persistence.database import Database
from core.utils.logger import log

# region  CLASS - CommandTracker

class CommandTracker:

    """Deterministic command lifecycle tracker."""

    def __init__(self, db: Database | None = None) -> None:
        """
        Args:
            db: optional core.persistence.database.Database instance.
                When provided, every command and its audit history
                is persisted, giving a durable command ledger that
                survives restarts (useful for post-incident review).
        """
# trace_id command record
        self._commands: dict[str, dict[str, Any]] = {}
        # globally seen event_ids (cross-trace dedup)
        self._seen_event_ids: set[str] = set()
        self._repo = None
        if db is not None:
            from core.persistence.repositories.command_repo import CommandRepository
            self._repo = CommandRepository(db)
            for trace_id, record in self._repo.get_all().items():
                self._commands[trace_id] = record
                for h in record["history"]:
                    eid = h["payload"].get("event_id") if isinstance(h["payload"], dict) else None
                    if eid:
                        self._seen_event_ids.add(eid)
            if self._commands:
                log(
                    "CommandTracker",
                    f"hydrated {len(self._commands)} command(s) from database",
                    channel="TRACKER",
                )

    # region  PUBLIC API

    def create(self, trace_id: str, command: dict[str, Any]) -> None:
        """Register a new command. Idempotent - safe to call twice."""
        if trace_id in self._commands:
            log("CommandTracker",
                f"create() called twice for {trace_id}",
                channel="TRACKER")
            return

        self._commands[trace_id] = {
            "command": command,
            "status":  "ISSUED",
            "history": [self._make_event("COMMAND_ISSUED", command)],
        }
        if self._repo is not None:
            self._repo.upsert_command(trace_id, command, "ISSUED")
            self._repo.append_history(trace_id, "COMMAND_ISSUED", command, time.time())
        log("CommandTracker", f"created trace_id={trace_id}", channel="TRACKER")

    def update(
        self,
        trace_id:   str,
        event_type: str,
        payload:    dict[str, Any] | None = None,
    ) -> bool:
        """Process an incoming event for a tracked command.

        Priority order (strict):
        1. trace_id must exist
        2. event_id must be present
        3. event_id must be globally unique
        4. event_type must map to a known state
        5. transition must be valid
        6. same-state events DUPLICATE_STATE_EVENT
        7. apply state
        Returns True only if step 7 was reached and the state was
        actually applied. Returns False for every rejection path
        (1-6) - callers MUST check this before acting on the event
        as if it were genuine (e.g. releasing a node's job, advancing
        a mission).
        """
        payload = payload or {} # pyright: ignore[reportUnknownVariableType]
        # 1. Unknown trace
        if trace_id not in self._commands:
            log("CommandTracker",
                f"unknown trace_id={trace_id} - dropped",
                channel="TRACKER")
            return False
        record = self._commands[trace_id]
        current_state = record["status"]
        # 2. Missing event_id
        event_id = payload.get("event_id") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if event_id is None:
            record["history"].append(self._make_event("INVALID_EVENT", {
                "reason": "missing_event_id",
                "event_type": event_type,
                "payload": payload,
            }))
            log("CommandTracker",
                f"missing event_id for {trace_id}",
                channel="TRACKER")
            return False
        # 3. Global dedup
        if event_id in self._seen_event_ids:
            record["history"].append(self._make_event("DUPLICATE_EVENT", {
                "event_id": event_id,
                "event_type": event_type,
            }))
            log("CommandTracker",
                f"duplicate event_id={event_id}",
                channel="TRACKER")
            return False
        self._seen_event_ids.add(event_id) # pyright: ignore[reportUnknownArgumentType]
        # 4. Unknown event type
        new_state = EVENT_TO_STATE.get(event_type)
        if new_state is None:
            record["history"].append(self._make_event("INVALID_EVENT", {
                "reason": "unknown_event_type",
                "event_type": event_type,
            }))
            log("CommandTracker",
                f"unknown event_type={event_type}",
                channel="TRACKER")
            return False
        # 5. Transition guard
        allowed = VALID_TRANSITIONS.get(current_state, set())
        if new_state not in allowed:
            record["history"].append(self._make_event("INVALID_TRANSITION", {
                "from": current_state,
                "to": new_state,
                "event": event_type,
            }))
            log("CommandTracker",
                f"invalid transition {current_state}{new_state} for {trace_id}",
                channel="TRACKER")
            return False
        # 6. Same-state detection
        if new_state == current_state:
            record["history"].append(self._make_event("DUPLICATE_STATE_EVENT", {
                "state": current_state,
                "event_type": event_type,
            }))
            return False
        # 7. Apply
        record["status"] = new_state
        record["history"].append(self._make_event(event_type, payload)) # pyright: ignore[reportUnknownArgumentType]
        if self._repo is not None:
            self._repo.update_status(trace_id, new_state)
            self._repo.append_history(trace_id, event_type, payload, time.time()) # pyright: ignore[reportUnknownArgumentType]
        log("CommandTracker",
            f"{trace_id}: {current_state} {new_state}",
            channel="TRACKER")
        return True

    def get(self, trace_id: str) -> dict[str, Any] | None:
        return self._commands.get(trace_id)

    def get_all(self) -> dict[str, Any]:
        return dict(self._commands)

    def get_issued(self) -> list[tuple[str, dict[str, Any]]]:
        """Return all commands currently in ISSUED state.
        Used by CommanderCore._expire_loop() to detect timed-out commands.
        Returns list of (trace_id, record) tuples.
        """
        return [
            (tid, rec) for tid, rec in self._commands.items()
            if rec.get("status") == "ISSUED"
        ]

    def is_terminal(self, trace_id: str) -> bool:
        record = self._commands.get(trace_id)
        return record is not None and record["status"] in TERMINAL_STATES

    # endregion

    # region  PRIVATE METHODS

    @staticmethod
    def _make_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type":      event_type,
            "payload":   payload,
            "timestamp": time.time(),
        }

    # endregion

# endregion (end of class CommandTracker)
