from __future__ import annotations

from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext
from core.node_registry.registry import NodeRegistry
from core.state.fire_state_store import FireRecord
from wfc_shared.schemas.commands import Command
from wfc_shared.enums.command_types import REASSIGN_LEADER, RESPOND_TO_FIRE

class LeaderLostRule(Rule):
    """
    Handles leader death with election-resolved guard and fallback.
    """

    @property
    def name(self) -> str:
        return "leader_lost"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        # GUARD: If fire already has an active assigned leader, skip (election resolved)
        if fire.assigned_nodes:
            active_count = sum(1 for lid in fire.assigned_nodes 
                               if registry.get(lid) and registry.get(lid).status == "ACTIVE")  # pyright: ignore[reportOptionalMemberAccess]
            if active_count > 0:
                return RuleResult(triggered=False, reason="election_already_resolved")

        # Check if any assigned leader is actually dead
        dead_leaders = [lid for lid in fire.assigned_nodes 
                        if registry.get(lid) is None or registry.get(lid).status != "ACTIVE"]  # pyright: ignore[reportOptionalMemberAccess]
        if not dead_leaders:
            return RuleResult(triggered=False, reason="all_leaders_alive")

        # Step 1: Look for LEADER_BACKUP in same zone (SAFE)
        zone = registry.get(dead_leaders[0]).zone if registry.get(dead_leaders[0]) else None  # pyright: ignore[reportOptionalMemberAccess]
        backups = [n for n in registry.get_all().values()
                   if "LEADER_BACKUP" in (n.capabilities or [])
                   and n.status == "ACTIVE"
                   and n.zone == zone]
        if backups:
            return RuleResult(
                triggered=True,
                reason=f"backup_{backups[0].node_id}_dispatched",
                commands=[
                    Command(
                        target_node=backups[0].node_id,
                        command_type=RESPOND_TO_FIRE,
                        payload={"fire_id": fire.fire_id}
                    )
                ]
            )

        # Step 2: Reassign from lower-priority fire (DISRUPTIVE - needs approval)
        # We return a command that will be held for approval by the engine
        return RuleResult(
            triggered=True,
            reason="no_backup_available_requesting_reassignment",
            commands=[
                Command(
                    target_node="SYSTEM",
                    command_type=REASSIGN_LEADER,
                    payload={"fire_id": fire.fire_id},
                    requires_approval=True  # Engine will route to pending store  # pyright: ignore[reportCallIssue]
                )
            ]
        )