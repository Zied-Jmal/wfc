# TEST: U-RULE-009 - NoRespondersRule

from __future__ import annotations

import pytest  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.capabilities import SWARM_LEAD, DISPATCH_COMMANDS
from wfc_shared.enums.fire_status import ACTIVE
from core.rule_engine.rules.no_responders import NoRespondersRule
from core.state.fire_state_store import FireRecord

class TestNoRespondersRule:
    def test_triggers_when_no_leaders_available(self) -> None:
        fire = FireRecord(
            fire_id="fire-nr-01", state=ACTIVE, zone="zone_a",
            severity="HIGH", sensor_id="s1"
        )
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("cmd-1", "COMMANDER", [DISPATCH_COMMANDS])
        reg.heartbeat("cmd-1")
        rule = NoRespondersRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered == True
        assert len(result.commands) == 1
        assert result.commands[0].command_type == "ESCALATE_FIRE"
        assert "NO_SWARM_LEADERS_AVAILABLE" in str(result.commands[0].payload)

    def test_no_trigger_when_leaders_available(self) -> None:
        fire = FireRecord(
            fire_id="fire-nr-02", state=ACTIVE, zone="zone_a",
            severity="MEDIUM", sensor_id="s1"
        )
        from core.node_registry.registry import NodeRegistry
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        rule = NoRespondersRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered == False
        assert result.reason == "swarm_leaders_available"

    def test_no_trigger_fire_already_assigned(self) -> None:
        fire = FireRecord(
            fire_id="fire-nr-03", state=ACTIVE, zone="zone_a",
            severity="MEDIUM", sensor_id="s1",
            assigned_nodes=["sl-1"]
        )
        from core.node_registry.registry import NodeRegistry
        rule = NoRespondersRule()
        result = rule.evaluate(fire, NodeRegistry())
        assert result.triggered == False
        assert result.reason == "fire_already_assigned"
