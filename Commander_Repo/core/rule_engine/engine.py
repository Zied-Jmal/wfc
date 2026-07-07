# Major upgrades:
# - Accepts RuleContext and passes it to all rules.
# - Replaces rule.requires_approval with COMMAND_RISK dict.
# - Adds all 15 rules (including new ones).
# - Supports telemetry_rules filter for efficient re-evaluation.
from __future__ import annotations

import inspect
import time

from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext
from core.node_registry.registry import NodeRegistry
from core.approval.approval_gate import ApprovalGate
from core.state.fire_state_store import FireStateStore, FireRecord
from core.persistence.repositories.alert_repo import AlertRepository
from core.state.domain_event_log import DomainEventLog

# --- Existing rules ---
from core.rule_engine.rules.fire_dispatch import FireDispatchRule
from core.rule_engine.rules.high_severity import HighSeverityRule
from core.rule_engine.rules.fire_suppressed import FireSuppressedRule
from core.rule_engine.rules.fire_contained import FireContainedRule
from core.rule_engine.rules.no_responders import NoRespondersRule

# --- New rules ---
from core.rule_engine.rules.elected_leader import ElectedLeaderRule
from core.rule_engine.rules.severity_increase import SeverityIncreaseRule
from core.rule_engine.rules.fire_expansion import FireExpansionRule
from core.rule_engine.rules.rekindled import RekindleDetectionRule
from core.rule_engine.rules.containment_failure import ContainmentFailureRule
from core.rule_engine.rules.fire_verification import FireVerificationRule
from core.rule_engine.rules.leader_lost import LeaderLostRule
from core.rule_engine.rules.swarm_attrition import SwarmAttritionRule
from core.rule_engine.rules.resource_exhaustion import ResourceExhaustionRule
from core.rule_engine.rules.priority import PriorityRule

from wfc_shared.enums.command_risk import COMMAND_RISK, CommandRisklevels
from wfc_shared.enums.command_types import RESPOND_TO_FIRE
from wfc_shared.enums.domain_event_types import FIRE_DISPATCHED, ESCALATION_REQUESTED
from wfc_shared.schemas.domain_event import DomainEvent
from core.utils.logger import log

class RuleEngine:
    """
    Evaluates all 15 rules with RuleContext.
    """

    def __init__(self, registry: NodeRegistry, gate: ApprovalGate, fires: FireStateStore | None = None, alerts: AlertRepository | None = None, event_log: DomainEventLog | None = None) -> None:
        self.registry = registry
        self.gate = gate
        self._fires = fires
        self._alerts = alerts
        self._event_log = event_log

        self._rules: list[Rule] = [
# Existing (5)
            FireDispatchRule(),
            HighSeverityRule(),
            NoRespondersRule(),
            FireContainedRule(),
            FireSuppressedRule(),
# New (10)
            ElectedLeaderRule(),
            SeverityIncreaseRule(),
            FireExpansionRule(),
            RekindleDetectionRule(),
            ContainmentFailureRule(),
            FireVerificationRule(),
            LeaderLostRule(),
            SwarmAttritionRule(),
            ResourceExhaustionRule(),
            PriorityRule(),
        ]
# Rule name lookup for filtering
        self._rule_by_name = {r.name: r for r in self._rules}

    def evaluate(self, fire: FireRecord, context: RuleContext | None = None) -> list[RuleResult]:
        """
        Evaluate all rules against a fire's current state.
        Context is optional but recommended - a minimal one is
        constructed if None is provided.
        """

        if context is None:
# Fallback for old callers (should not happen in v2.2)
            from core.rule_engine.context import RuleContext
            from core.rule_engine.trigger import EvalTrigger
            context = RuleContext(trigger=EvalTrigger.MANUAL, event_log=self._event_log)  # pyright: ignore[reportArgumentType]

        results: list[RuleResult] = []

# Filter rules if telemetry_rules is specified
        rules_to_run = self._rules
        if context.telemetry_rules is not None:
            rules_to_run = [
                r for r in self._rules
                if r.name in context.telemetry_rules
            ]

        log("RuleEngine",
            f"evaluating fire_id={fire.fire_id[:8]} "
            f"state={fire.state} severity={fire.severity} trigger={context.trigger}",
            channel="RULES")

# HIGH_FIRE alert - once per fire
        if self._alerts is not None and fire.severity == "HIGH":
            self._alerts.add(
                alert_id=f"high_fire:{fire.fire_id}",
                kind="HIGH_FIRE",
                severity="CRITICAL",
                title=f"HIGH severity fire in {fire.zone}",
                detail=f"sensor={fire.sensor_id} fire_id={fire.fire_id} state={fire.state}",
                source_ref=fire.fire_id,
            )

        for rule in rules_to_run:
            try:
# Call rule.evaluate with context if it accepts it
# Otherwise fall back to old signature
                sig = inspect.signature(rule.evaluate)  # pyright: ignore[reportCallIssue]
                if "context" in sig.parameters:
                    result = rule.evaluate(fire, self.registry, context)  # pyright: ignore[reportCallIssue]
                else:
# Old-style rule (should be updated eventually)
                    result = rule.evaluate(fire, self.registry)

                self._log_result(rule.name, fire, result)

                if result.triggered:
                    for command in result.commands:
# --- NEW APPROVAL MODEL (COMMAND_RISK) ---
                        risk = COMMAND_RISK.get(command.command_type, CommandRisklevels.SAFE)
                        requires_approval = (risk != CommandRisklevels.SAFE)

# Idempotency: for ESCALATE_FIRE, check event log
                        if requires_approval and self._event_log is not None:
                            if self._event_log.has_event(fire.fire_id, ESCALATION_REQUESTED):
                                log("RuleEngine",
                                    f"[{rule.name}] ESCALATION_REQUESTED already logged "
                                    f"for fire={fire.fire_id[:8]} - skipping duplicate",
                                    channel="RULES")
                                continue
                            self._event_log.append(DomainEvent(
                                event_type=ESCALATION_REQUESTED,  # pyright: ignore[reportArgumentType]
                                fire_id=fire.fire_id,
                                reason=f"escalation_requested_by_{rule.name}",
                            ))

                        ref = self.gate.submit(command, requires_approval=requires_approval)

# Mark node busy for RESPOND_TO_FIRE
                        if command.command_type == RESPOND_TO_FIRE:
                            self.registry.assign_job(command.target_node, fire.fire_id)
                            if self._fires is not None:
                                self._fires.add_assigned_node(
                                    fire.fire_id,
                                    command.target_node,
                                    reason=f"dispatched_to_{command.target_node}",
                                )
                            if self._event_log is not None:
                                self._event_log.append(DomainEvent(
                                    event_type=FIRE_DISPATCHED,  # pyright: ignore[reportArgumentType]
                                    fire_id=fire.fire_id,
                                    node_id=command.target_node,
                                    reason=f"dispatched_by_{rule.name}",
                                    payload={"command_type": command.command_type},
                                ))

# Alert for pending approvals
                        if requires_approval and self._alerts is not None:
                            self._alerts.add(
                                alert_id=f"pending:{ref}",
                                kind="PENDING_APPROVAL",
                                severity="WARNING",
                                title=f"Approval required: {command.command_type} → {command.target_node}",
                                detail=f"fire_id={fire.fire_id} risk={risk}",
                                source_ref=ref,
                            )
# Apply state updates directly (for rules that modify state)
                    if result.state_updates and self._fires is not None:
                        fire_id = fire.fire_id
                        updates = result.state_updates

                        if "state" in updates:
                            new_state = updates["state"]
# Use transition() to change the fire's state
                            self._fires.transition(
                                fire_id,
                                new_state,
                                reason=f"rule_{rule.name}_{result.reason[:30]}"
                            )
                            log("RuleEngine",
                                f"[{rule.name}] applied state update: {fire.state} → {new_state}",
                                channel="RULES")
# If the rule requested assigned_nodes to be cleared
# (e.g. ContainmentFailureRule re-activates a fire whose
# leader died - clearing lets FireDispatchRule re-dispatch)
                            if updates.get("clear_assigned_nodes"):
                                self._fires.assign_node(fire_id, None, f"[{rule.name}] cleared assigned_nodes on re-activation")

                        if "severity" in updates:
                            self._fires.update_severity(fire_id, updates["severity"], f"[{rule.name}] severity update")

# PriorityRule: remove stolen leader from old fire's assigned_nodes.
# NOTE: registry.assign_job() was already called above when
# processing the RESPOND_TO_FIRE command, overwriting the old
# current_job with the new fire_id. Do NOT call release_job()
# here - that would undo the new assignment.
                        if "preempt_from_fire_id" in updates and "preempt_node_id" in updates:
                            old_fire_id = updates["preempt_from_fire_id"]
                            stolen_node = updates["preempt_node_id"]
                            self._fires.remove_assigned_node(old_fire_id, stolen_node, f"[{rule.name}] preempted {stolen_node}")
                            log("RuleEngine",
                                f"[{rule.name}] preempted {stolen_node} from fire {old_fire_id[:8]}",
                                channel="RULES")
                results.append(result)
                if self._fires is not None:
                    latest_fire = self._fires.get(fire.fire_id)
                    if latest_fire is not None:
                        fire = latest_fire
            except Exception as exc:
                log("RuleEngine",
                    f"rule '{rule.name}' raised: {exc}",
                    channel="RULES")
                results.append(RuleResult(triggered=False, reason=f"rule_error: {exc}"))

        return results

    def _log_result(self, rule_name: str, fire: FireRecord, result: RuleResult) -> None:
        status = "TRIGGERED" if result.triggered else "skipped"
        log("RuleEngine",
            f"[{rule_name}] {status} | "
            f"fire_id={fire.fire_id[:8]} state={fire.state} "
            f"reason={result.reason}",
            channel="RULES")
