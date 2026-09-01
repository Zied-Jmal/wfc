# TEST: U-APPR-001 to U-APPR-004

from __future__ import annotations

import time
from unittest.mock import MagicMock

from wfc_shared.schemas.commands import Command
from wfc_shared.schemas.pending import PendingCommand


class TestPendingCommandStore:
    def test_add_sets_future_expires_at(self) -> None:
        from core.approval.pending_store import PendingCommandStore

        mock_dispatcher = MagicMock()
        mock_mqtt = MagicMock()
        store = PendingCommandStore(mock_dispatcher, mock_mqtt, "node-1", ttl=30.0)
        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE", payload={})
        pending = PendingCommand(command=cmd, created_at=1000.0)
        # before add, expires_at is None
        assert pending.expires_at is None
        store.add(pending)
        retrieved = store.get(pending.pending_id)
        assert retrieved is not None
        assert retrieved.expires_at == 1030.0

    def test_expire_stale_only_expires_pending(self) -> None:
        from core.approval.pending_store import PendingCommandStore

        mock_dispatcher = MagicMock()
        mock_mqtt = MagicMock()
        store = PendingCommandStore(mock_dispatcher, mock_mqtt, "node-1", ttl=1.0)
        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE", payload={})
        # Fresh pending (not expired)
        fresh = PendingCommand(command=cmd, created_at=time.time(), expires_at=time.time() + 3600)
        store._store["fresh-pending"] = fresh  # pyright: ignore[reportPrivateUsage]
        # Expired pending
        expired = PendingCommand(command=cmd, created_at=time.time() - 100, expires_at=time.time() - 1)
        store._store["expired-pending"] = expired  # pyright: ignore[reportPrivateUsage]
        # Already approved
        approved = PendingCommand(
            command=cmd, created_at=time.time() - 100, expires_at=time.time() - 1, status="APPROVED"
        )
        store._store["approved-pending"] = approved  # pyright: ignore[reportPrivateUsage]

        result = store.expire_stale()
        assert "expired-pending" in result
        assert "fresh-pending" not in result
        assert "approved-pending" not in result


class TestApprovalHandler:
    def test_routes_approved_correctly(self) -> None:
        from core.approval.approval_handler import ApprovalHandler
        from core.approval.pending_store import PendingCommandStore

        mock_dispatcher = MagicMock()
        mock_mqtt = MagicMock()
        store = PendingCommandStore(mock_dispatcher, mock_mqtt, "node-1")
        handler = ApprovalHandler(store)
        store._store["p1"] = MagicMock()  # pyright: ignore[reportPrivateUsage]
        store._store["p1"].status = "PENDING"  # pyright: ignore[reportPrivateUsage]

        handler.handle({"pending_id": "p1", "decision": "APPROVED", "operator_id": "op-1"})
        # approve() was called - dispatcher.send should have been called
        mock_dispatcher.send.assert_called_once()

    def test_routes_rejected_correctly(self) -> None:
        from core.approval.approval_handler import ApprovalHandler
        from core.approval.pending_store import PendingCommandStore

        mock_dispatcher = MagicMock()
        mock_mqtt = MagicMock()
        store = PendingCommandStore(mock_dispatcher, mock_mqtt, "node-1")
        handler = ApprovalHandler(store)
        store._store["p2"] = MagicMock()  # pyright: ignore[reportPrivateUsage]
        store._store["p2"].status = "PENDING"  # pyright: ignore[reportPrivateUsage]

        handler.handle({"pending_id": "p2", "decision": "REJECTED", "reason": "too_risky"})
        mock_dispatcher.send.assert_not_called()

    def test_drops_unknown_decision(self) -> None:
        from core.approval.approval_handler import ApprovalHandler
        from core.approval.pending_store import PendingCommandStore

        mock_dispatcher = MagicMock()
        mock_mqtt = MagicMock()
        store = PendingCommandStore(mock_dispatcher, mock_mqtt, "node-1")
        handler = ApprovalHandler(store)
        store._store["p3"] = MagicMock()  # pyright: ignore[reportPrivateUsage]
        store._store["p3"].status = "PENDING"  # pyright: ignore[reportPrivateUsage]

        handler.handle({"pending_id": "p3", "decision": "MAYBE"})
        mock_dispatcher.send.assert_not_called()


class TestApprovalGate:
    def test_submit_routes_to_dispatcher_when_not_requiring_approval(self) -> None:
        from core.approval.approval_gate import ApprovalGate

        mock_dispatcher = MagicMock()
        mock_dispatcher.send.return_value = "trace-abc"
        mock_store = MagicMock()
        gate = ApprovalGate(mock_dispatcher, mock_store)
        cmd = Command(target_node="sl-1", command_type="RESPOND_TO_FIRE", payload={})
        result = gate.submit(cmd, requires_approval=False)
        assert result == "trace-abc"
        mock_dispatcher.send.assert_called_once_with(cmd)
        mock_store.add.assert_not_called()

    def test_submit_routes_to_store_when_requiring_approval(self) -> None:
        from core.approval.approval_gate import ApprovalGate

        mock_dispatcher = MagicMock()
        mock_store = MagicMock()
        gate = ApprovalGate(mock_dispatcher, mock_store)
        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE", payload={})
        gate.submit(cmd, requires_approval=True)  # pyright: ignore[reportUnusedVariable]
        mock_store.add.assert_called_once()
        mock_dispatcher.send.assert_not_called()
