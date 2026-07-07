# TEST: U-RULE-013 - ContainmentFailureRule

from __future__ import annotations

import pytest  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.fire_status import CONTAINED, ACTIVE
from core.rule_engine.rules.containment_failure import ContainmentFailureRule
from core.rule_engine.context import RuleContext
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord

class TestContainmentFailureRule:
    def test_reactivates_when_leader_dead(self) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        # Register leader but mark as dead by not heartbeating - or just not add it
        fire = FireRecord(
            fire_id="fire-cf-01", state=CONTAINED, zone="zone_a",
            severity="MEDIUM", sensor_id="s1",
            assigned_nodes=["dead-leader"]
        )
        ctx = RuleContext(trigger=EvalTrigger.TELEMETRY_UPDATE, event_log=None)  # pyright: ignore
        rule = ContainmentFailureRule()
        result = rule.evaluate(fire, reg, ctx)
        assert result.triggered == True
        assert result.state_updates is not None
        assert result.state_updates.get("state") == ACTIVE
        assert result.state_updates.get("clear_assigned_nodes") == True

    def test_no_trigger_when_leader_alive(self) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        fire = FireRecord(
            fire_id="fire-cf-02", state=CONTAINED, zone="zone_a",
            severity="MEDIUM", sensor_id="s1",
            assigned_nodes=["sl-1"]
        )
        ctx = RuleContext(trigger=EvalTrigger.TELEMETRY_UPDATE, event_log=None)  # pyright: ignore
        rule = ContainmentFailureRule()
        result = rule.evaluate(fire, reg, ctx)
        assert result.triggered == False
        assert result.reason == "containment_holding"

    def test_no_assigned_leader_triggers_failure(self) -> None:
        from core.node_registry.registry import NodeRegistry
        fire = FireRecord(
            fire_id="fire-cf-03", state=CONTAINED, zone="zone_a",
            severity="MEDIUM", sensor_id="s1"
        )
        ctx = RuleContext(trigger=EvalTrigger.TELEMETRY_UPDATE, event_log=None)  # pyright: ignore
        rule = ContainmentFailureRule()
        result = rule.evaluate(fire, NodeRegistry(), ctx)
        assert result.triggered == True
        assert "no_assigned_leader" in result.reason
