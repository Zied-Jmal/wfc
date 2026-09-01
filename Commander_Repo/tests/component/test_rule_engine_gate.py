# CI-CMD-001: RuleEngine + ApprovalGate: HIGH severity fire is gated
# CI-CMD-002: RuleEngine + FireStateStore: RESPOND_TO_FIRE causes assign_job + add_assigned_node

from __future__ import annotations

from typing import Any

from core.approval.approval_gate import ApprovalGate
from core.approval.pending_store import PendingCommandStore
from core.rule_engine.context import RuleContext
from core.rule_engine.engine import RuleEngine
from core.rule_engine.trigger import EvalTrigger


class TestRuleEngineWithGate:
    def test_high_severity_fire_is_gated(
        self, registry_with_leaders: Any, fire_state: Any, high_fire_record: Any, fake_dispatcher: Any, fake_mqtt: Any
    ) -> None:
        store = PendingCommandStore(fake_dispatcher, fake_mqtt, "node-1")
        gate = ApprovalGate(fake_dispatcher, store)
        engine = RuleEngine(registry_with_leaders, gate, fires=fire_state)
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None)  # pyright: ignore
        results = engine.evaluate(high_fire_record, ctx)

        # HighSeverityRule should trigger but not dispatch directly
        triggered = [r for r in results if r.triggered]
        assert len(triggered) >= 1
        # The ESCALATE_FIRE command should have been held, not dispatched
        # Check that pending_store has at least 1 pending item
        assert len(store.get_all_pending()) >= 1

    def test_fire_dispatch_causes_dual_write(
        self, registry_with_leaders: Any, fire_state: Any, fake_dispatcher: Any, fake_mqtt: Any
    ) -> None:
        from wfc_shared.enums.fire_status import ACTIVE

        fire_from_store = fire_state.ignite("fire-dispatch-01", "zone_alpha", "MEDIUM", "s1", (36.80, 10.18))
        # Transition to ACTIVE so FireDispatchRule triggers
        fire_from_store = fire_state.transition("fire-dispatch-01", ACTIVE)
        assert fire_from_store is not None

        store = PendingCommandStore(fake_dispatcher, fake_mqtt, "node-1")
        gate = ApprovalGate(fake_dispatcher, store)
        engine = RuleEngine(registry_with_leaders, gate, fires=fire_state)
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None)  # pyright: ignore

        engine.evaluate(fire_from_store, ctx)  # pyright: ignore[reportUnusedVariable]

        # Check registry: target should have current_job set
        # Check fire_state: fire should have assigned_nodes populated
        fire_after = fire_state.get("fire-dispatch-01")
        assert fire_after is not None
        assert len(fire_after.assigned_nodes) >= 1
        target = fire_after.assigned_nodes[0]
        node = registry_with_leaders.get(target)
        assert node is not None
        assert node.current_job == "fire-dispatch-01"
