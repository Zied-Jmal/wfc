"""Tests for SwarmAttritionRule — verifies dict iteration fix and rule logic."""

from __future__ import annotations

import time
import pytest

from wfc_shared.enums.fire_status import ACTIVE
from wfc_shared.enums.command_types import REINFORCE_FIRE, ESCALATE_FIRE
from wfc_shared.schemas.telemetry import SwarmStatusSnapshot
from core.state.fire_state_store import FireRecord
from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.trigger import EvalTrigger
from core.rule_engine.rules.swarm_attrition import SwarmAttritionRule


@pytest.fixture
def fire() -> FireRecord:
    return FireRecord(
        fire_id="fire-attr-001",
        state=ACTIVE,
        zone="zone_alpha",
        severity="HIGH",
        sensor_id="s1",
        assigned_nodes=["sl-1"],
    )


@pytest.fixture
def registry() -> NodeRegistry:
    reg = NodeRegistry()
    reg.register("sl-1", "SWARM_LEADER", ["SWARM_LEAD"], zone="zone_alpha")
    reg.register("sl-2", "SWARM_LEADER", ["SWARM_LEAD"], zone="zone_alpha")
    reg.heartbeat("sl-1")
    reg.heartbeat("sl-2")
    return reg


class TestSwarmAttritionRule:
    def test_triggers_on_lost_drones(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = SwarmAttritionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-attr-001",
            timestamp=time.time(),
            active_drones=1,
            lost_drones=4,
            avg_battery_pct=0.8,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is True
        assert result.commands[0].command_type == REINFORCE_FIRE

    def test_triggers_on_low_battery(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = SwarmAttritionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-attr-001",
            timestamp=time.time(),
            active_drones=3,
            lost_drones=0,
            avg_battery_pct=0.15,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is True

    def test_escalates_when_no_available_leaders(self, fire: FireRecord) -> None:
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEAD", ["SWARM_LEAD"])
        reg.heartbeat("sl-1")
        rule = SwarmAttritionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-attr-001",
            timestamp=time.time(),
            lost_drones=5,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert result.triggered is True
        assert result.commands[0].command_type == ESCALATE_FIRE

    def test_not_triggered_when_healthy(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = SwarmAttritionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-attr-001",
            timestamp=time.time(),
            active_drones=3,
            lost_drones=0,
            avg_battery_pct=0.8,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is False

    def test_skips_on_wrong_trigger(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = SwarmAttritionRule()
        ctx = RuleContext(
            trigger=EvalTrigger.NEW_FIRE,
            event_log=None,  # pyright: ignore[reportArgumentType]
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is False
