"""fire_suppressed.py
FireSuppressedRule - stand down assigned node on suppression
- Trigger on FIRE_SUPPRESSED events
- Send STAND_DOWN to the ONE assigned swarm leader
- Close the fire response lifecycle
"""

from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import STAND_DOWN

# Standard Library
# Third-Party Libraries
# Project Imports
from wfc_shared.enums.fire_status import SUPPRESSED
from wfc_shared.schemas.commands import Command

# region  CLASS - FireSuppressedRule


class FireSuppressedRule(Rule):
    """
    Triggers on FIRE_SUPPRESSED. Sends STAND_DOWN to the single swarm
    leader assigned to this fire (fire.assigned_node) to close the
    fire response lifecycle.
    """

    # region  RULE METADATA

    @property
    def name(self) -> str:
        return "fire_suppressed"

    @property
    def requires_approval(self) -> bool:
        return False

    # endregion

    # region  EVALUATION
    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        """
        Targets only fire.assigned_node - the single swarm leader
        assigned to this fire. Safe under concurrent fires.
        """

        if fire.state != SUPPRESSED:
            return RuleResult(triggered=False, reason=f"fire_state_is_{fire.state}_not_suppressed")

        target = fire.assigned_node
        if not target:
            return RuleResult(triggered=False, reason="no_assigned_node_to_stand_down")

        return RuleResult(
            triggered=True,
            reason=f"standing_down_{target}",
            commands=[
                Command(
                    target_node=target,
                    command_type=STAND_DOWN,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": fire.fire_id,
                        "zone": fire.zone,
                        "location": fire.zone,
                        "reason": "fire_suppressed",
                    },
                )
            ],
        )

    # endregion


# endregion (end of class FireSuppressedRule)
