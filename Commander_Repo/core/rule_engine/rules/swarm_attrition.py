from __future__ import annotations

from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext
from core.node_registry.registry import NodeRegistry
from core.state.fire_state_store import FireRecord
from wfc_shared.schemas.commands import Command
from wfc_shared.enums.command_types import REINFORCE_FIRE, ESCALATE_FIRE
from core.rule_engine.trigger import EvalTrigger

class SwarmAttritionRule(Rule):
    """Reinforces if drones lost or battery critically low."""

    @property
    def name(self) -> str:
        return "swarm_attrition"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.TELEMETRY_UPDATE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_telemetry")

        for snap in context.swarm_snapshots.values():  # pyright: ignore[reportOptionalMemberAccess]
            if snap.fire_id != fire.fire_id:
                continue

            if snap.lost_drones > 3 or snap.avg_battery_pct < 0.2:
                # Try to reinforce first
                available = [n for n in registry.get_all().values() 
                             if "SWARM_LEAD" in n.capabilities and n.status == "ACTIVE"
                             and n.node_id not in fire.assigned_nodes]
                if available:
                    return RuleResult(
                        triggered=True,
                        reason="reinforcing_due_to_attrition",
                        commands=[
                            Command(
                                target_node=available[0].node_id,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                                command_type=REINFORCE_FIRE,  # pyright: ignore[reportArgumentType]
                                payload={"fire_id": fire.fire_id}
                            )
                        ]
                    )
                else:
                    # No available leader -> escalate
                    return RuleResult(
                        triggered=True,
                        reason="escalate_due_to_no_available_leaders",
                        commands=[
                            Command(
                                target_node="SYSTEM",
                                command_type=ESCALATE_FIRE,  # pyright: ignore[reportArgumentType]
                                payload={"fire_id": fire.fire_id}
                            )
                        ]
                    )
        return RuleResult(triggered=False, reason="swarm_healthy")