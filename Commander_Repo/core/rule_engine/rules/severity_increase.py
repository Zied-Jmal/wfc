from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.fire_status import ACTIVE, SPREADING


class SeverityIncreaseRule(Rule):
    """
    Updates fire severity and transitions to SPREADING if intensity increased.
    """

    @property
    def name(self) -> str:
        return "severity_increase"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.INTENSITY_UPDATE:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_intensity_trigger")

        # context.trigger_payload must be set by the handler
        update = getattr(context, "_intensity_payload", None)
        if not update:
            return RuleResult(triggered=False, reason="no_intensity_data")

        new_intensity = update.get("new_intensity")
        if new_intensity == fire.severity:
            return RuleResult(triggered=False, reason="severity_unchanged")

        # We return a special result that tells the engine to update severity.
        # The engine will handle the state mutation.
        return RuleResult(
            triggered=True,
            reason=f"severity_increased_to_{new_intensity}",
            state_updates={
                "severity": new_intensity,
                "transition_to": SPREADING if fire.state == ACTIVE else fire.state,
            },
        )
