"""no_responders.py
NoRespondersRule - escalate when no responders available.

Triggers on fire.state == ACTIVE with no available leader.
Runs after FireDispatchRule. Only triggers if
get_by_capability(SWARM_LEAD) is empty.
"""

from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.capabilities import DISPATCH_COMMANDS, SWARM_LEAD
from wfc_shared.enums.command_types import ESCALATE_FIRE

# Standard Library
# Third-Party Libraries
# Project Imports
from wfc_shared.enums.fire_status import ACTIVE
from wfc_shared.schemas.commands import Command

# region  CLASS - NoRespondersRule


class NoRespondersRule(Rule):
    """Triggers on FIRE_DETECTED only when no SWARM_LEAD nodes
    are available. Escalates to DISPATCH_COMMANDS nodes (commanders,
    operators) so a human is notified immediately."""

    # region  RULE METADATA

    @property
    def name(self) -> str:
        return "no_responders"

    @property
    def requires_approval(self) -> bool:
        return False

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if fire.state != ACTIVE:
            return RuleResult(triggered=False, reason=f"fire_state_is_{fire.state}_not_active")

        if fire.assigned_node is not None:
            return RuleResult(triggered=False, reason="fire_already_assigned")

        available = registry.get_available(SWARM_LEAD)
        if available:
            return RuleResult(triggered=False, reason="swarm_leaders_available")

        escalation_targets = registry.get_by_capability(DISPATCH_COMMANDS)
        if not escalation_targets:
            return RuleResult(triggered=False, reason="no_escalation_target_either")

        return RuleResult(
            triggered=True,
            reason="no_swarm_leaders_available - escalating",
            commands=[
                Command(
                    target_node=target,
                    command_type=ESCALATE_FIRE,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id": fire.fire_id,
                        "zone": fire.zone,
                        "location": fire.zone,
                        "severity": fire.severity,
                        "reason": "NO_SWARM_LEADERS_AVAILABLE",
                    },
                )
                for target in escalation_targets
            ],
        )

    # endregion


# endregion (end of class NoRespondersRule)
