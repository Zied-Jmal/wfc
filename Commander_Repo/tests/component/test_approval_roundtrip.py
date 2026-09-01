# CI-CMD-003: Full approval round-trip

from __future__ import annotations

from unittest.mock import MagicMock

from core.approval.approval_gate import ApprovalGate  # pyright: ignore[reportUnusedImport]
from core.approval.approval_handler import ApprovalHandler
from core.approval.pending_store import PendingCommandStore
from wfc_shared.schemas.commands import Command


class TestApprovalRoundTrip:
    def test_full_approve_round_trip(self) -> None:
        fake_dispatcher = MagicMock()
        fake_dispatcher.send.return_value = "trace-abc"
        fake_mqtt = MagicMock()

        store = PendingCommandStore(fake_dispatcher, fake_mqtt, "node-1")
        gate = ApprovalGate(fake_dispatcher, store)
        handler = ApprovalHandler(store)

        # Submit a command requiring approval
        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE", payload={"fire_id": "fire-1"})
        pending_id = gate.submit(cmd, requires_approval=True)
        assert pending_id is not None

        # Verify it's in pending store
        pending = store.get(pending_id)
        assert pending is not None
        assert pending.status == "PENDING"

        # Approve it
        handler.handle({"pending_id": pending_id, "decision": "APPROVED", "operator_id": "op-1"})

        # Verify dispatcher was called exactly once
        fake_dispatcher.send.assert_called_once()

        # Verify MQTT had both PENDING and APPROVED publishes
        pend_publishes = [c for c in fake_mqtt.publish.call_args_list if c[0][1].get("event") == "COMMAND_PENDING"]
        approv_publishes = [c for c in fake_mqtt.publish.call_args_list if c[0][1].get("event") == "COMMAND_APPROVED"]
        assert len(pend_publishes) >= 1
        assert len(approv_publishes) >= 1

    def test_rejection_round_trip(self) -> None:
        fake_dispatcher = MagicMock()
        fake_mqtt = MagicMock()

        store = PendingCommandStore(fake_dispatcher, fake_mqtt, "node-1")
        gate = ApprovalGate(fake_dispatcher, store)
        handler = ApprovalHandler(store)

        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE", payload={})
        pending_id = gate.submit(cmd, requires_approval=True)

        # Reject it
        handler.handle({"pending_id": pending_id, "decision": "REJECTED", "reason": "too_risky", "operator_id": "op-1"})

        # Dispatcher should NOT have been called
        fake_dispatcher.send.assert_not_called()

        # Should have REJECTED publish
        rej_publishes = [c for c in fake_mqtt.publish.call_args_list if c[0][1].get("event") == "COMMAND_REJECTED"]
        assert len(rej_publishes) >= 1
