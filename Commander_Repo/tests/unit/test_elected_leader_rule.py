# TEST: U-RULE-010 - ElectedLeaderRule: stale term rejection
# TEST: U-RULE-011 - ElectedLeaderRule: accepts valid newer term

from __future__ import annotations

from typing import Any

import pytest

from core.rule_engine.context import RuleContext
from core.rule_engine.rules.elected_leader import ElectedLeaderRule
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.fire_status import ACTIVE


class TestElectedLeaderRule:
    @pytest.fixture
    def fire(self):
        return FireRecord(
            fire_id="fire-el-01", state=ACTIVE, zone="zone_a", severity="MEDIUM", sensor_id="s1", leader_term=3
        )

    @pytest.fixture
    def rule(self):
        return ElectedLeaderRule()

    def test_rejects_stale_term(self, fire: Any, rule: Any) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        ctx = RuleContext(
            trigger=EvalTrigger.ELECTION_RESULT,
            event_log=None,  # pyright: ignore
            election_metadata={"term": 2, "fire_id": fire.fire_id, "new_leader_id": "sl-2"},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert not result.triggered
        assert "stale_term" in result.reason

    def test_rejects_equal_term(self, fire: Any, rule: Any) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        ctx = RuleContext(
            trigger=EvalTrigger.ELECTION_RESULT,
            event_log=None,  # pyright: ignore
            election_metadata={"term": 3, "fire_id": fire.fire_id, "new_leader_id": "sl-2"},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert not result.triggered
        assert "stale_term" in result.reason

    def test_accepts_valid_newer_term(self, rule: Any) -> None:
        from core.node_registry.registry import NodeRegistry

        fire = FireRecord(
            fire_id="fire-el-02", state=ACTIVE, zone="zone_a", severity="LOW", sensor_id="s1", leader_term=1
        )
        reg = NodeRegistry()
        reg.register("sl-9", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-9")
        ctx = RuleContext(
            trigger=EvalTrigger.ELECTION_RESULT,
            event_log=None,  # pyright: ignore
            election_metadata={"term": 2, "fire_id": fire.fire_id, "new_leader_id": "sl-9"},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert result.triggered
        assert len(result.commands) == 1
        assert result.commands[0].command_type == "CONFIRM_LEADERSHIP"
        assert result.commands[0].target_node == "sl-9"

    def test_rejects_incapable_node(self, rule: Any) -> None:
        from core.node_registry.registry import NodeRegistry

        fire = FireRecord(
            fire_id="fire-el-03", state=ACTIVE, zone="zone_a", severity="LOW", sensor_id="s1", leader_term=1
        )
        reg = NodeRegistry()
        reg.register("drone-1", "SCOUT_DRONE", ["SCOUT"])
        reg.heartbeat("drone-1")
        ctx = RuleContext(
            trigger=EvalTrigger.ELECTION_RESULT,
            event_log=None,  # pyright: ignore
            election_metadata={"term": 2, "fire_id": fire.fire_id, "new_leader_id": "drone-1"},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert not result.triggered
        assert result.reason == "node_not_capable"

    def test_rejects_fire_id_mismatch(self, rule: Any) -> None:
        from core.node_registry.registry import NodeRegistry

        fire = FireRecord(
            fire_id="fire-el-04", state=ACTIVE, zone="zone_a", severity="LOW", sensor_id="s1", leader_term=1
        )
        reg = NodeRegistry()
        reg.register("sl-9", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-9")
        ctx = RuleContext(
            trigger=EvalTrigger.ELECTION_RESULT,
            event_log=None,  # pyright: ignore
            election_metadata={"term": 2, "fire_id": "other-fire", "new_leader_id": "sl-9"},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert not result.triggered
        assert result.reason == "fire_id_mismatch"
