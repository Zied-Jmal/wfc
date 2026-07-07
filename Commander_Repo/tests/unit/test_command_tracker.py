# TEST: U-TRACK-001 to U-TRACK-003

from __future__ import annotations

import pytest  # pyright: ignore[reportUnusedImport]
from core.commands_monitor.command_tracker import CommandTracker

class TestCommandTracker:
    def test_update_rejects_unknown_trace(self) -> None:
        tracker = CommandTracker()
        result = tracker.update("unknown-trace", "COMMAND_RECEIVED", {"event_id": "e1"})  # pyright: ignore[reportUnknownMemberType]
        assert result == False

    def test_update_rejects_duplicate_event_id(self) -> None:
        tracker = CommandTracker()
        payload = {"command_type": "RESPOND_TO_FIRE", "target_node": "sl-1"}
        tracker.create("trace-A", payload)
        tracker.create("trace-B", payload)
        # First update with event_id succeeds
        assert tracker.update("trace-A", "COMMAND_RECEIVED", {"event_id": "shared-id"}) == True  # pyright: ignore[reportUnknownMemberType]
        # Second update with same event_id on different trace must fail
        assert tracker.update("trace-B", "COMMAND_RECEIVED", {"event_id": "shared-id"}) == False  # pyright: ignore[reportUnknownMemberType]

    def test_enforces_state_machine(self) -> None:
        tracker = CommandTracker()
        tracker.create("trace-1", {"command_type": "RESPOND_TO_FIRE"})

        # ISSUED -> RECEIVED (valid)
        assert tracker.update("trace-1", "COMMAND_RECEIVED", {"event_id": "e1"}) == True  # pyright: ignore[reportUnknownMemberType]
        assert tracker.get("trace-1")["status"] == "RECEIVED"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownMemberType]

        # RECEIVED -> RECEIVED again (invalid, same-state)
        assert tracker.update("trace-1", "COMMAND_RECEIVED", {"event_id": "e2"}) == False  # pyright: ignore[reportUnknownMemberType]

        # RECEIVED -> EXECUTED (valid)
        assert tracker.update("trace-1", "COMMAND_EXECUTED", {"event_id": "e3"}) == True  # pyright: ignore[reportUnknownMemberType]
        assert tracker.get("trace-1")["status"] == "EXECUTED"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownMemberType]

        # EXECUTED -> anything (terminal, invalid)
        assert tracker.update("trace-1", "COMMAND_FAILED", {"event_id": "e4"}) == False  # pyright: ignore[reportUnknownMemberType]

    def test_is_terminal(self) -> None:
        tracker = CommandTracker()
        tracker.create("trace-1", {})
        assert tracker.is_terminal("trace-1") == False
        tracker.update("trace-1", "COMMAND_RECEIVED", {"event_id": "e1"})  # pyright: ignore[reportUnknownMemberType]
        tracker.update("trace-1", "COMMAND_EXECUTED", {"event_id": "e2"})  # pyright: ignore[reportUnknownMemberType]
        assert tracker.is_terminal("trace-1") == True

    def test_get_all(self) -> None:
        tracker = CommandTracker()
        tracker.create("t1", {})
        tracker.create("t2", {})
        assert len(tracker.get_all()) == 2
