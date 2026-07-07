"""
Rule base class and RuleResult.
Rules are stateless - they receive the current fire state (FireRecord)
and the node registry, and return a RuleResult. No side effects,
no MQTT, no I/O.
Rules read state, not events. The event still triggers RuleEngine.evaluate()
but rules never see it directly - they only see the current FireStateStore
snapshot for that fire (E2/E3 compliance).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from wfc_shared.schemas.commands import Command
from core.state.fire_state_store import FireRecord
from core.node_registry.registry import NodeRegistry
from core.rule_engine.context import RuleContext


# region  DATACLASS - RuleResult

@dataclass
class RuleResult:
    triggered: bool
    commands:  list[Command] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    reason:    str           = ""   # always set - logged regardless of triggered state
    state_updates: dict[str, Any]|None = None

# endregion


# region  CLASS - Rule (ABC)

class Rule(ABC):

    """
    Abstract base for all rule engine rules.

    Rules are stateless - they receive the CURRENT FIRE STATE (FireRecord)
    and the node registry, and return a RuleResult. No side effects,
    no MQTT, no I/O.
    """

    # region  ABSTRACT INTERFACE

    @abstractmethod
    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        """
        Evaluate this rule against the current fire STATE and registry.
        Must always return a RuleResult - never raise.

        Args:
            fire:     current FireRecord snapshot from FireStateStore.
                      fire.state tells you IGNITED/ACTIVE/SPREADING/
                      CONTAINED/SUPPRESSED/EXTINGUISHED - check that,
                      not an event_type.
            registry: NodeRegistry - current node state.
            context:  optional RuleEvaluationContext for richer
                      evaluation (telemetry, triggers, etc.).
        """

        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable rule name used in logs."""
        ...

    # endregion

    # region  APPROVAL HOOK

    @property
    def requires_approval(self) -> bool:
        """
        Whether commands produced by this rule must pass through the
        human approval gate before being dispatched.
        Default: False. Override to True for rules needing operator sign-off.
        """
        return False

    # endregion

# endregion (end of class Rule)
