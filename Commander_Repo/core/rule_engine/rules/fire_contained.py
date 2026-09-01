"""fire_contained.py
FireContainedRule - contain perimeter on fire contained
- Trigger on FIRE_CONTAINED events
- Send CONTAIN_FIRE to the ONE assigned swarm leader
- Instructs field nodes to hold perimeter (not stand down)
Fire lifecycle:
FIRE_DETECTED FireDispatchRule RESPOND_TO_FIRE
FIRE_CONTAINED FireContainedRule CONTAIN_FIRE
FIRE_SUPPRESSED FireSuppressedRule STAND_DOWN
Note: CONTAINED != SUPPRESSED. Contained means the fire is
no longer spreading but not yet out. Nodes must hold
the perimeter until SUPPRESSED is declared.
"""

from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.command_types import CONTAIN_FIRE
from wfc_shared.enums.fire_status import CONTAINED
from wfc_shared.schemas.commands import Command

# region  CLASS - FireContainedRule


class FireContainedRule(Rule):
    """
    Triggers on FIRE_CONTAINED. Sends CONTAIN_FIRE to the single swarm
    leader assigned to this fire (fire.assigned_node) - hold the
    perimeter, don't stand down yet.
    """

    # region  RULE METADATA

    @property
    def name(self) -> str:
        return "fire_contained"

    @property
    def requires_approval(self) -> bool:
        return False

    # endregion

    # region  EVALUATION
    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        """
        Targets ONLY fire.assigned_node - the one swarm leader
        FireDispatchRule actually sent RESPOND_TO_FIRE to for this
        specific fire. If no node is assigned, the rule reports
        not-triggered instead of broadcasting.
        """

        if fire.state != CONTAINED:
            return RuleResult(triggered=False, reason=f"fire_state_is_{fire.state}_not_contained")

        target = fire.assigned_node
        if not target:
            return RuleResult(triggered=False, reason="no_assigned_node_to_contain")

        return RuleResult(
            triggered=True,
            reason=f"containing_via_{target}",
            commands=[
                Command(
                    target_node=target,
                    command_type=CONTAIN_FIRE,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": fire.fire_id,
                        "zone": fire.zone,
                        "location": fire.zone,
                        "location_coords": fire.location_coords,
                        "reason": "fire_contained",
                    },
                )
            ],
        )

    # endregion


# endregion (end of class FireContainedRule)
