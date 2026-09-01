from __future__ import annotations

from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.fire_status import ACTIVE, EXTINGUISHED, SUPPRESSED


class RekindleDetectionRule(Rule):
    """Re-ignites fire if REKINDLED event arrives."""

    @property
    def name(self) -> str:
        return "rekindle_detection"

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if context.trigger != EvalTrigger.REKINDLED:  # pyright: ignore[reportOptionalMemberAccess]
            return RuleResult(triggered=False, reason="not_rekindled_trigger")

        if fire.state not in (SUPPRESSED, EXTINGUISHED):
            return RuleResult(triggered=False, reason="not_suppressed")

        return RuleResult(
            triggered=True,
            reason="fire_rekindled",
            state_updates={
                "state": ACTIVE,
                "rekindled_at": context._rekindled_payload.get("timestamp"),  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess, reportUnknownMemberType]
            },
        )
