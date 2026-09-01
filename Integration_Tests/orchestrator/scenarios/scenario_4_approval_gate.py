"""
SCENARIO 4 \u2014 Human Approval Gate
===================================
Validates the ApprovalGate / PendingCommandStore / ApprovalHandler flow in
the Commander. Uses a real HIGH-severity FireEvent to trigger the RuleEngine
-> HighSeverityRule -> ESCALATE_FIRE (IRREVERSIBLE) path, which the approval
gate intercepts before forwarding.
NOTE: No rule currently produces ABORT_MISSION, so the scenario exercises
the gate with ESCALATE_FIRE instead. The gate mechanism (risk lookup ->
COMMAND_PENDING -> operator approval -> dispatch) is identical for all
IRREVERSIBLE command types.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from orchestrator.engine import Scenario, Stage

DASHBOARD_HTTP: str = "http://dashboard:8080"
EVENTS_FIRE: str = "wfc/events/fire"
APPROVAL_PENDING: str = "wfc/approval/pending"


def build(leader_id: str = "sl-A-01") -> Scenario:

    def trigger_high_severity_fire(ctx: dict[str, Any]) -> dict[str, Any]:
        """Publish a real FireEvent (HIGH severity) to trigger the
        RuleEngine -> HighSeverityRule -> ESCALATE_FIRE approval path."""
        fire_id: str = str(uuid.uuid4())[:8]
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "event_type": "FIRE_DETECTED",
            "timestamp": time.time(),
            "source": "test-harness-sensor",
            "payload": {
                "fire_id": fire_id,
                "zone": "zone_alpha",
                "severity": "HIGH",
                "sensor_id": "test-harness-sensor",
                "location_coords": [36.8065, 10.1815],
            },
        }
        ctx["_publish_now"] = (EVENTS_FIRE, event, 1, False)
        return {"fire_id": fire_id}

    stage_a = Stage(
        stage_id="A_high_severity_fire_detected",
        name="HIGH-severity fire detected",
        component="Test Harness",
        expect_desc="Publish FireEvent (HIGH severity) to wfc/events/fire",
        subscribe_topics=[EVENTS_FIRE],
        timeout_s=5.0,
        on_enter=trigger_high_severity_fire,
        match_fn=lambda topic, payload, ctx: isinstance(payload, dict),  # pyright: ignore[reportUnnecessaryIsInstance]
    )

    stage_b = Stage(
        stage_id="B_approval_pending_published",
        name="Commander intercepts ESCALATE_FIRE at approval gate",
        component="ApprovalGate / PendingCommandStore (Commander repo)",
        expect_desc=f"COMMAND_PENDING for ESCALATE_FIRE arrives on {APPROVAL_PENDING}",
        subscribe_topics=[APPROVAL_PENDING],
        timeout_s=15.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("event") == "COMMAND_PENDING"
            and payload.get("command_type") == "ESCALATE_FIRE"
        ),
        extract_ctx=lambda topic, payload, ctx: {
            "pending_id": payload.get("pending_id"),
        },
        on_skip=lambda ctx: {"pending_id": ctx.get("pending_id") or "synthetic-pending-id"},
    )

    async def approve_via_dashboard(ctx: dict[str, Any]) -> dict[str, Any]:
        pending_id: Any = ctx.get("pending_id")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{DASHBOARD_HTTP}/api/approval/respond",
                    json={"request_id": pending_id, "approved": True, "reason": "test-harness-approve"},
                )
                return {"approval_http_status": resp.status_code}
        except Exception as exc:
            return {"approval_http_error": str(exc)}

    stage_c = Stage(
        stage_id="C_operator_approves_via_dashboard",
        name="Operator approves via Dashboard REST API",
        component="POST /api/approval/respond (Dashboard repo)",
        expect_desc="Dashboard publishes decision=APPROVED to wfc/approval/response",
        subscribe_topics=["wfc/approval/response"],
        timeout_s=8.0,
        on_enter=lambda ctx: None,
        active_check=approve_via_dashboard,
        active_check_interval_s=2.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("pending_id") == ctx.get("pending_id")
            and payload.get("decision") == "APPROVED"
        ),
    )

    stage_d = Stage(
        stage_id="D_commander_dispatches_approved_command",
        name="Commander dispatches approved ESCALATE_FIRE",
        component="CommandDispatcher post-approval (Commander repo)",
        expect_desc="ESCALATE_FIRE dispatched on wfc/command/{target_node}",
        subscribe_topics=["wfc/command/#"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict) and payload.get("command_type") == "ESCALATE_FIRE"  # pyright: ignore[reportUnnecessaryIsInstance]
        ),
    )

    return Scenario(
        scenario_id="approval_gate",
        title="Human Approval Gate",
        description=(
            "Triggers ESCALATE_FIRE via HIGH-severity FireEvent, verifies "
            "ApprovalGate intercepts it, operator approves via Dashboard REST, "
            "and commander dispatches the approved command."
        ),
        stages=[stage_a, stage_b, stage_c, stage_d],
    )
