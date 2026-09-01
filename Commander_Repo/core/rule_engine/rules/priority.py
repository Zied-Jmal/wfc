from __future__ import annotations

from typing import ClassVar

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import RESPOND_TO_FIRE, STAND_DOWN
from wfc_shared.schemas.commands import Command


class PriorityRule(Rule):
    """
    Preempts leader from lowest-priority fire when a higher-priority fire
    has no available leader.

    Priority order: CRITICAL > HIGH > SPREADING > MEDIUM > ACTIVE > LOW > CONTAINED

    Emits STAND_DOWN to the stolen leader (for old fire) and RESPOND_TO_FIRE
    to that same leader (for the new fire). Also signals the engine to
    remove the leader from the old fire's assigned_nodes via state_updates.
    """

    @property
    def name(self) -> str:
        return "priority"

    _priority_map: ClassVar[dict[str, int]] = {
        "CRITICAL": 100,
        "HIGH": 90,
        "SPREADING": 85,
        "MEDIUM": 70,
        "ACTIVE": 60,
        "LOW": 30,
        "CONTAINED": 10,
    }

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.NEW_FIRE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_new_fire")

        # No available leaders?
        available = [
            n
            for n in registry.get_all().values()
            if "SWARM_LEAD" in n.capabilities and n.status == "ACTIVE" and n.current_job is None
        ]
        if available:
            return RuleResult(triggered=False, reason="leaders_available")

        # Already assigned skip
        if fire.assigned_nodes:
            return RuleResult(triggered=False, reason="fire_already_assigned")

        fires_store = getattr(context, "fires_store", None)
        if fires_store is None:
            return RuleResult(triggered=False, reason="no_fires_store_in_context")

        all_fires = fires_store.get_active()
        if not all_fires:
            return RuleResult(triggered=False, reason="no_other_fires")

        candidate_fires = [f for f in all_fires if f.assigned_nodes and f.fire_id != fire.fire_id]
        if not candidate_fires:
            return RuleResult(triggered=False, reason="no_leader_to_steal")

        lowest_fire = min(candidate_fires, key=lambda f: self._priority_map.get(f.severity, 0))
        if self._priority_map.get(lowest_fire.severity, 0) >= self._priority_map.get(fire.severity, 0):
            return RuleResult(triggered=False, reason="new_fire_lower_priority")

        stolen_leader = lowest_fire.assigned_nodes[0]
        return RuleResult(
            triggered=True,
            reason=f"preempting_{stolen_leader}_from_{lowest_fire.fire_id[:8]}",
            commands=[
                Command(
                    target_node=stolen_leader,
                    command_type=STAND_DOWN,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": lowest_fire.fire_id,
                        "reason": f"preempted_for_higher_priority_fire_{fire.fire_id}",
                    },
                ),
                Command(
                    target_node=stolen_leader,
                    command_type=RESPOND_TO_FIRE,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": fire.fire_id,
                        "zone": fire.zone,
                        "location": fire.zone,
                        "location_coords": fire.location_coords,
                        "severity": fire.severity,
                        "sensor_id": fire.sensor_id,
                        "preempted_from": lowest_fire.fire_id,
                    },
                ),
            ],
            state_updates={
                "preempt_from_fire_id": lowest_fire.fire_id,
                "preempt_node_id": stolen_leader,
            },
        )
