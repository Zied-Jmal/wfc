"""high_severity.py
HighSeverityRule - escalate HIGH severity fires
- Trigger on FIRE_DETECTED with severity == HIGH
- Send ESCALATE_FIRE to all DISPATCH_COMMANDS nodes
- Require human approval before dispatch
"""

from __future__ import annotations

# Standard Library

# Third-Party Libraries

# Project Imports

from wfc_shared.enums.fire_status import ACTIVE, IGNITED
from wfc_shared.enums.capabilities import DISPATCH_COMMANDS
from wfc_shared.enums.command_types import ESCALATE_FIRE
from wfc_shared.schemas.commands import Command
from core.state.fire_state_store import FireRecord
from core.node_registry.registry import NodeRegistry
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext

# Any non-terminal, non-contained/suppressed state still warrants escalation
# review if severity is HIGH - covers a HIGH fire the instant it's known,
# regardless of which exact pre-containment state it's in.
_ESCALATABLE_STATES = {IGNITED, ACTIVE}

# region  CLASS - HighSeverityRule

class HighSeverityRule(Rule):

    """
    Triggers when a fire's CURRENT severity is HIGH and it's still in an
    active/escalatable state. Sends ESCALATE_FIRE to all DISPATCH_COMMANDS
    nodes. Requires operator approval before dispatch.
    """

    # region  RULE METADATA

    @property
    def name(self) -> str:
        return "high_severity"

    @property
    def requires_approval(self) -> bool:
        return True

    # endregion

    # region  EVALUATION
    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if fire.state not in _ESCALATABLE_STATES:
            return RuleResult(triggered=False, reason=f"fire_state_{fire.state}_not_escalatable")

        if fire.severity not in ("HIGH", "CRITICAL"):
            return RuleResult(
                triggered=False,
                reason=f"severity_is_{fire.severity.lower()}_not_high",
            )

        targets = registry.get_by_capability(DISPATCH_COMMANDS)
        if not targets:
            return RuleResult(triggered=False, reason="no_backup_commander_available")

        return RuleResult(
            triggered=True,
            reason=f"escalating_to_{len(targets)}_commander(s)",
            commands=[
                Command(
                    target_node=node,
                    command_type=ESCALATE_FIRE,  # pyright: ignore[reportArgumentType]
                    payload={
                        "fire_id":  fire.fire_id,
                        "zone":     fire.zone,
                        "location": fire.zone,   # back-compat alias
                        "severity": fire.severity,
                    },
                )
                for node in targets
            ],
        )

    # endregion

# endregion (end of class HighSeverityRule)
