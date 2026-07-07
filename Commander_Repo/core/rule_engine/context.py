from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from core.rule_engine.trigger import EvalTrigger
from core.state.domain_event_log import DomainEventLog
from wfc_shared.schemas.telemetry import SwarmStatusSnapshot

@dataclass
class RuleContext:
    trigger: EvalTrigger
    event_log: DomainEventLog
    swarm_snapshots: dict[str, SwarmStatusSnapshot] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    election_metadata: (dict[str, Any] | None) = None
    telemetry_rules: (list[str] | None) = None
    fires_store: (Any | None) = None  # FireStateStore - used by PriorityRule