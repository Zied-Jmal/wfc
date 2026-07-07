# TEST: U-RULE-012 - PriorityRule

from __future__ import annotations
from typing import Any

import pytest
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.fire_status import ACTIVE
from core.rule_engine.rules.priority import PriorityRule
from core.rule_engine.context import RuleContext
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord, FireStateStore

class TestPriorityRule:
    @pytest.fixture
    def fires_store(self):
        store = FireStateStore()
        # Other fire with MEDIUM severity already assigned
        store._fires["other-fire"] = FireRecord(  # pyright: ignore[reportPrivateUsage]
            fire_id="other-fire", state=ACTIVE, zone="zone_a",
            severity="MEDIUM", sensor_id="s1",
            assigned_nodes=["sl-1"]
        )
        return store

    def test_preempts_lower_priority_fire(self, fires_store: Any) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        reg.assign_job("sl-1", "other-fire")  # busy on other fire
        # New critical fire with no available leaders
        new_fire = FireRecord(
            fire_id="new-critical", state=ACTIVE, zone="zone_b",
            severity="CRITICAL", sensor_id="s2"
        )
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None,  # pyright: ignore
                          fires_store=fires_store)
        rule = PriorityRule()
        result = rule.evaluate(new_fire, reg, ctx)
        assert result.triggered == True
        # Should have 2 commands: STAND_DOWN + RESPOND_TO_FIRE
        assert len(result.commands) == 2
        assert result.commands[0].command_type == "STAND_DOWN"
        assert result.commands[1].command_type == "RESPOND_TO_FIRE"
        assert result.commands[0].target_node == "sl-1"
        assert result.commands[1].target_node == "sl-1"

    def test_no_preempt_for_lower_priority(self, fires_store: Any) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        reg.assign_job("sl-1", "other-fire")
        new_fire = FireRecord(
            fire_id="new-low", state=ACTIVE, zone="zone_b",
            severity="LOW", sensor_id="s2"
        )
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None,  # pyright: ignore
                          fires_store=fires_store)
        rule = PriorityRule()
        result = rule.evaluate(new_fire, reg, ctx)
        assert result.triggered == False
        assert result.reason == "new_fire_lower_priority"

    def test_no_preempt_when_leaders_available(self) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        # Leader is idle (no job) - no preemption needed
        new_fire = FireRecord(
            fire_id="new-fire", state=ACTIVE, zone="zone_b",
            severity="CRITICAL", sensor_id="s2"
        )
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None,  # pyright: ignore
                          fires_store=FireStateStore())
        rule = PriorityRule()
        result = rule.evaluate(new_fire, reg, ctx)
        assert result.triggered == False
        assert result.reason == "leaders_available"

    def test_not_new_fire_trigger(self, fires_store: Any) -> None:
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        new_fire = FireRecord(
            fire_id="new-fire", state=ACTIVE, zone="zone_b",
            severity="CRITICAL", sensor_id="s2"
        )
        ctx = RuleContext(trigger=EvalTrigger.NODE_AVAILABLE, event_log=None,  # pyright: ignore
                          fires_store=fires_store)
        rule = PriorityRule()
        result = rule.evaluate(new_fire, reg, ctx)
        assert result.triggered == False
        assert result.reason == "not_new_fire"
