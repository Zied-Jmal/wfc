from __future__ import annotations

from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext
from core.node_registry.registry import NodeRegistry
from core.state.fire_state_store import FireRecord
from wfc_shared.schemas.commands import Command
from wfc_shared.enums.command_types import REINFORCE_FIRE
from core.rule_engine.trigger import EvalTrigger

class FireExpansionRule(Rule):
    """
    Reinforces fire if spread rate is RAPID and we don't already have two leaders.
    """

    @property
    def name(self) -> str:
        return "fire_expansion"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.TELEMETRY_UPDATE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_telemetry_trigger")

        snapshots = context.swarm_snapshots  # pyright: ignore[reportOptionalMemberAccess]
        if not snapshots:
            return RuleResult(triggered=False, reason="no_snapshots")

        # If we already have 2+ leaders, don't reinforce again
        if len(fire.assigned_nodes) >= 2:
            return RuleResult(triggered=False, reason="already_reinforced")

        for snap in snapshots.values():
            if snap.fire_id != fire.fire_id:
                continue
            if snap.spread_rate == "RAPID":
                # Find an available leader (not already assigned)
                # registry.get_all() returns dict[str, NodeRecord]
                # We need to iterate over the VALUES
                available = [
                    n for n in registry.get_all().values()
                    if "SWARM_LEAD" in (n.capabilities or [])
                    and n.status == "ACTIVE"
                    and n.node_id not in fire.assigned_nodes
                ]
                if not available:
                    return RuleResult(triggered=False, reason="no_available_leader")

                return RuleResult(
                    triggered=True,
                    reason=f"rapid_spread_detected_by_{snap.leader_id}",
                    commands=[
                        Command(
                            target_node=available[0].node_id,
                            command_type=REINFORCE_FIRE,  # pyright: ignore[reportArgumentType]
                            payload={"fire_id": fire.fire_id}
                        )
                    ]
                )
        return RuleResult(triggered=False, reason="spread_not_rapid")