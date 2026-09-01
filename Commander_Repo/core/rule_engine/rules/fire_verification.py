from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.capabilities import SCOUT
from wfc_shared.enums.command_types import DISPATCH_DRONE
from wfc_shared.schemas.commands import Command


class FireVerificationRule(Rule):
    """Sends scout if fire severity is LOW."""

    @property
    def name(self) -> str:
        return "fire_verification"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.NEW_FIRE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_new_fire")

        if fire.severity != "LOW":
            return RuleResult(triggered=False, reason="severity_not_low")

        # ✅ Iterate over .values() to get NodeRecord objects
        scouts = [n for n in registry.get_all().values() if SCOUT in (n.capabilities or []) and n.status == "ACTIVE"]
        if not scouts:
            return RuleResult(triggered=False, reason="no_scout_available")

        # Dispatch the nearest scout (simplified: pick first)
        return RuleResult(
            triggered=True,
            reason="scout_dispatched_for_verification",
            commands=[
                Command(
                    target_node=scouts[0].node_id,
                    command_type=DISPATCH_DRONE,  # pyright: ignore[reportArgumentType]
                    payload={"fire_id": fire.fire_id, "task": "VERIFY", "location": list(fire.location_coords or [])},
                )
            ],
        )
