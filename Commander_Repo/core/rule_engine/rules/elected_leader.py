from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import CONFIRM_LEADERSHIP
from wfc_shared.schemas.commands import Command


class ElectedLeaderRule(Rule):
    """Accepts bully election results via term comparison."""

    @property
    def name(self) -> str:
        return "elected_leader"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        meta = context.election_metadata  # pyright: ignore[reportOptionalMemberAccess]
        if not meta:
            return RuleResult(triggered=False, reason="no_election_metadata")

        if meta.get("term", 0) <= fire.leader_term:
            return RuleResult(triggered=False, reason=f"stale_term_{meta.get('term')}")

        if fire.fire_id != meta.get("fire_id"):
            return RuleResult(triggered=False, reason="fire_id_mismatch")

        new_leader = meta.get("new_leader_id")
        rec = registry.get(new_leader)  # pyright: ignore[reportArgumentType]
        if rec is None or "SWARM_LEAD" not in (rec.capabilities or []):
            return RuleResult(triggered=False, reason="node_not_capable")

        return RuleResult(
            triggered=True,
            reason=f"bully_election_term_{meta['term']}_leader_{new_leader}",
            commands=[
                Command(
                    target_node=new_leader,  # pyright: ignore[reportArgumentType]
                    command_type=CONFIRM_LEADERSHIP,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": fire.fire_id,
                        "current_state": fire.state,
                        "severity": fire.severity,
                        "location_coords": list(fire.location_coords or []),
                        "term_accepted": meta["term"],
                    },
                )
            ],
        )
