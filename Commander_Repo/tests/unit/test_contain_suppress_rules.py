# TEST: U-RULE-007 - FireContainedRule: targets assigned leader only
# TEST: U-RULE-008 - FireSuppressedRule: targets assigned leader only

from __future__ import annotations

from core.rule_engine.rules.fire_contained import FireContainedRule
from core.rule_engine.rules.fire_suppressed import FireSuppressedRule
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.fire_status import CONTAINED, SUPPRESSED


class TestFireContainedRule:
    def test_targets_assigned_leader_only(self) -> None:
        fire = FireRecord(
            fire_id="fire-ct-01",
            state=CONTAINED,
            zone="zone_a",
            severity="MEDIUM",
            sensor_id="s1",
            assigned_nodes=["sl-1"],
        )
        # Register many SWARM_LEAD nodes
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        for nid in ["sl-1", "sl-2", "sl-3"]:
            reg.register(nid, "SWARM_LEADER", [SWARM_LEAD])
            reg.heartbeat(nid)
        rule = FireContainedRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered
        assert len(result.commands) == 1
        assert result.commands[0].target_node == "sl-1"
        assert result.commands[0].command_type == "CONTAIN_FIRE"

    def test_no_assigned_node(self) -> None:
        fire = FireRecord(fire_id="fire-ct-02", state=CONTAINED, zone="zone_a", severity="MEDIUM", sensor_id="s1")
        from core.node_registry.registry import NodeRegistry

        rule = FireContainedRule()
        result = rule.evaluate(fire, NodeRegistry())
        assert not result.triggered
        assert "no_assigned_node" in result.reason


class TestFireSuppressedRule:
    def test_targets_assigned_leader_only(self) -> None:
        fire = FireRecord(
            fire_id="fire-sp-01",
            state=SUPPRESSED,
            zone="zone_a",
            severity="MEDIUM",
            sensor_id="s1",
            assigned_nodes=["sl-2"],
        )
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        for nid in ["sl-1", "sl-2", "sl-3"]:
            reg.register(nid, "SWARM_LEADER", [SWARM_LEAD])
            reg.heartbeat(nid)
        rule = FireSuppressedRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered
        assert len(result.commands) == 1
        assert result.commands[0].target_node == "sl-2"
        assert result.commands[0].command_type == "STAND_DOWN"
