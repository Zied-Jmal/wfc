from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import ESCALATE_FIRE, REINFORCE_FIRE, STAND_DOWN
from wfc_shared.schemas.commands import Command


class ResourceExhaustionRule(Rule):
    """Rotates swarm if payload is nearly empty."""

    @property
    def name(self) -> str:
        return "resource_exhaustion"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.TELEMETRY_UPDATE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_telemetry")

        for snap in context.swarm_snapshots.values():  # pyright: ignore[reportOptionalMemberAccess]
            if snap.fire_id != fire.fire_id:
                continue

            if snap.avg_payload_litres < 1.5:
                commands = []
                # Stand down current leaders
                for lid in fire.assigned_nodes:
                    commands.append(  # pyright: ignore[reportUnknownMemberType]
                        Command(
                            target_node=lid,
                            command_type=STAND_DOWN,  # pyright: ignore[reportArgumentType]
                            payload={"fire_id": fire.fire_id},
                        )
                    )
                # Find fresh leader
                fresh = [
                    n
                    for n in registry.get_all().values()
                    if "SWARM_LEAD" in n.capabilities and n.status == "ACTIVE" and n.node_id not in fire.assigned_nodes
                ]
                if fresh:
                    commands.append(  # pyright: ignore[reportUnknownMemberType]
                        Command(
                            target_node=fresh[0].node_id,  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                            command_type=REINFORCE_FIRE,  # pyright: ignore[reportArgumentType]
                            payload={"fire_id": fire.fire_id},
                        )
                    )
                else:
                    commands.append(  # pyright: ignore[reportUnknownMemberType]
                        Command(
                            target_node="SYSTEM",
                            command_type=ESCALATE_FIRE,  # pyright: ignore[reportArgumentType]
                            payload={"fire_id": fire.fire_id},
                        )
                    )
                return RuleResult(
                    triggered=True,
                    reason="resource_exhaustion",
                    commands=commands,  # pyright: ignore[reportUnknownArgumentType]
                )
        return RuleResult(triggered=False, reason="payload_sufficient")
