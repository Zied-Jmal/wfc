"""Tests for ResourceExhaustionRule — verifies dict iteration fix and rule logic."""

from __future__ import annotations

import time

import pytest

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rules.resource_exhaustion import ResourceExhaustionRule
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import ESCALATE_FIRE, REINFORCE_FIRE, STAND_DOWN
from wfc_shared.enums.fire_status import ACTIVE
from wfc_shared.schemas.telemetry import SwarmStatusSnapshot


@pytest.fixture
def fire() -> FireRecord:
    return FireRecord(
        fire_id="fire-res-001",
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


class TestResourceExhaustionRule:
    def test_stands_down_and_reinforces(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = ResourceExhaustionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-res-001",
            timestamp=time.time(),
            avg_payload_litres=1.0,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is True
        assert len(result.commands) == 2
        assert result.commands[0].command_type == STAND_DOWN
        assert result.commands[1].command_type == REINFORCE_FIRE

    def test_escalates_when_no_fresh_leader(self, fire: FireRecord) -> None:
        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEAD", ["SWARM_LEAD"])
        reg.heartbeat("sl-1")
        rule = ResourceExhaustionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-res-001",
            timestamp=time.time(),
            avg_payload_litres=0.5,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, reg, ctx)
        assert result.triggered is True
        assert result.commands[0].command_type == STAND_DOWN
        assert result.commands[1].command_type == ESCALATE_FIRE

    def test_not_triggered_when_payload_sufficient(self, fire: FireRecord, registry: NodeRegistry) -> None:
        rule = ResourceExhaustionRule()
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-res-001",
            timestamp=time.time(),
            avg_payload_litres=5.0,
        )
        ctx = RuleContext(
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            event_log=None,  # pyright: ignore[reportArgumentType]
            swarm_snapshots={"sl-1": snap},
        )
        result = rule.evaluate(fire, registry, ctx)
        assert result.triggered is False
