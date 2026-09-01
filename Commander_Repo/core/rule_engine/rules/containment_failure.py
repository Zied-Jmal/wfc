from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.fire_status import ACTIVE, CONTAINED


class ContainmentFailureRule(Rule):
    """
    Re-activates fire if CONTAINED but has no assigned leader or leader dead.
    """

    @property
    def name(self) -> str:
        return "containment_failure"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if fire.state != CONTAINED:
            return RuleResult(triggered=False, reason="not_contained")

        # Case 1: No assigned leader - abnormal for CONTAINED, treat as failure
        if len(fire.assigned_nodes) == 0:
            return RuleResult(
                triggered=True,
                reason="contained_fire_has_no_assigned_leader",
                state_updates={"state": ACTIVE, "clear_assigned_nodes": True},
            )

        # Case 2: Any assigned leader is dead or not ACTIVE
        for leader_id in fire.assigned_nodes:
            node = registry.get(leader_id)
            if node is None or node.status != "ACTIVE":
                return RuleResult(
                    triggered=True,
                    reason=f"leader_{leader_id}_dead_on_contained_fire",
                    state_updates={"state": ACTIVE, "clear_assigned_nodes": True},
                )

        # Optional: telemetry shows spread not slow
        if context.trigger == EvalTrigger.TELEMETRY_UPDATE:  # pyright: ignore[reportOptionalMemberAccess]
            for snap in context.swarm_snapshots.values():  # pyright: ignore[reportOptionalMemberAccess]
                if snap.fire_id == fire.fire_id and snap.spread_rate not in (None, "SLOW"):
                    return RuleResult(triggered=True, reason="contained_but_spreading", state_updates={"state": ACTIVE})

        return RuleResult(triggered=False, reason="containment_holding")
