"""
SCENARIO 1 — Fire Dispatch End-to-End
=======================================
The flagship flow. Validates G-05 from PROJECT_MAP.md.

  [Trigger]  Test harness publishes a FireEvent (simulating a ground sensor)
  Stage A    Commander's RuleEngine reacts → publishes RESPOND_TO_FIRE
             to the nearest swarm leader's command topic.
  Stage B    Swarm Leader ACKs RECEIVED.
  Stage C    Swarm Leader ACKs EXECUTED (tactics ran, assignments dispatched).
  Stage D    A drone (scout or fighter) receives DISPATCH_DRONE/UPDATE_TASK
             from the leader.
  Stage E    The drone ACKs EXECUTED back to the leader.
  Stage F    The drone starts publishing telemetry tagged with the fire's
             leader_id (proves the whole loop is alive end-to-end).

Each stage is independently verifiable — if the commander never dispatches,
stages B-F will all time out / can be skipped, and you'll see exactly that
"RuleEngine -> Leader" link is broken without losing visibility into
whether the swarm side would have worked.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from orchestrator.engine import Scenario, Stage

EVENTS_FIRE: str = "wfc/events/fire"


def build(leader_id: str = "sl-A-01") -> Scenario:

    def trigger_fire_event(ctx: dict[str, Any]) -> dict[str, Any]:
        """Published when Stage A starts \u2014 simulates a ground sensor."""
        fire_id: str = str(uuid.uuid4())[:8]
        payload: dict[str, Any] = {
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
        ctx["_publish_now"] = (EVENTS_FIRE, payload, 1, False)
        return {"fire_id": fire_id, "trigger_ts": time.time()}

    stage_a = Stage(
        stage_id="A_commander_dispatch",
        name="Commander RuleEngine dispatches RESPOND_TO_FIRE",
        component="Commander / RuleEngine",
        expect_desc=f"Commander publishes a RESPOND_TO_FIRE command to wfc/command/{leader_id}",
        subscribe_topics=[f"wfc/command/{leader_id}"],
        timeout_s=15.0,
        on_enter=trigger_fire_event,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("command_type") == "RESPOND_TO_FIRE"
        ),
        extract_ctx=lambda topic, payload, ctx: {
            "trace_id_a": payload.get("trace_id"),
        },
        on_skip=lambda ctx: _synthetic_respond_to_fire(ctx, leader_id),
    )

    stage_b = Stage(
        stage_id="B_leader_ack_received",
        name="Swarm Leader ACKs RECEIVED",
        component="SwarmLeaderNode / FieldNode",
        expect_desc="Leader publishes ACK with status=RECEIVED for the RESPOND_TO_FIRE trace_id",
        subscribe_topics=["wfc/ack"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == leader_id
            and payload.get("status") == "RECEIVED"
        ),
        on_skip=lambda ctx: None,
    )

    stage_c = Stage(
        stage_id="C_leader_ack_executed",
        name="Swarm Leader ACKs EXECUTED",
        component="SwarmLeaderNode._execute_command",
        expect_desc="Leader publishes ACK with status=EXECUTED \u2014 tactics ran successfully",
        subscribe_topics=["wfc/ack"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == leader_id
            and payload.get("status") == "EXECUTED"
        ),
        on_skip=lambda ctx: _synthetic_drone_command(ctx),
    )

    stage_d = Stage(
        stage_id="D_drone_receives_command",
        name="A drone receives DISPATCH_DRONE/UPDATE_TASK",
        component="FieldNode (drone side)",
        expect_desc="Leader publishes a command to wfc/command/{drone_id} for some drone",
        subscribe_topics=["wfc/command/+"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("command_type") in ("DISPATCH_DRONE", "UPDATE_TASK", "RECALL_DRONE")
            and topic != f"wfc/command/{leader_id}"
        ),
        extract_ctx=lambda topic, payload, ctx: {
            "drone_id": topic.split("/")[-1],
            "drone_trace_id": payload.get("trace_id"),
        },
    )

    stage_e = Stage(
        stage_id="E_drone_ack_executed",
        name="Drone ACKs EXECUTED",
        component="FieldNode (drone side)",
        expect_desc="The dispatched drone confirms execution of its command",
        subscribe_topics=["wfc/ack"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == ctx.get("drone_id")
            and payload.get("status") == "EXECUTED"
        ),
    )

    stage_f = Stage(
        stage_id="F_drone_telemetry_flowing",
        name="Drone telemetry tagged with correct leader_id",
        component="ScoutDroneNode / FirefightingDroneNode telemetry loop",
        expect_desc="Telemetry from the dispatched drone arrives with leader_id matching the active leader",
        subscribe_topics=["wfc/telemetry/+"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("drone_id") == ctx.get("drone_id")
            and payload.get("leader_id") == leader_id
        ),
    )

    return Scenario(
        scenario_id="fire_dispatch",
        title="Fire Dispatch End-to-End",
        description=(
            "Sensor detects fire \u2192 Commander dispatches to nearest leader \u2192 "
            "Leader assigns drones \u2192 Drone executes and reports telemetry. "
            "Validates the full G-05 flow from PROJECT_MAP.md."
        ),
        stages=[stage_a, stage_b, stage_c, stage_d, stage_e, stage_f],
    )


def _synthetic_respond_to_fire(ctx: dict[str, Any], leader_id: str) -> dict[str, Any]:
    """If the commander never dispatched, manually push the next stage's
    trigger so downstream stages (leader ACK etc.) can still be probed.
    """
    trace_id: str = str(uuid.uuid4())
    ctx["_publish_now"] = (
        f"wfc/command/{leader_id}",
        {
            "trace_id": trace_id,
            "target_node": leader_id,
            "command_type": "RESPOND_TO_FIRE",
            "payload": {
                "fire_id": ctx.get("fire_id", "skip-fire"),
                "location": [36.8065, 10.1815],
                "severity": "HIGH",
            },
            "from": "test-harness-skip-injector",
            "timestamp": time.time(),
        },
        1,
        False,
    )
    return {"trace_id_a": trace_id, "skip_injected_stage_a": True}


def _synthetic_drone_command(ctx: dict[str, Any]) -> dict[str, Any]:
    """If the leader never reached EXECUTED, inject a synthetic drone
    command so Stage D/E/F can still be probed independently.
    """
    drone_id: str = "sd-A-01"
    trace_id: str = str(uuid.uuid4())
    ctx["_publish_now"] = (
        f"wfc/command/{drone_id}",
        {
            "trace_id": trace_id,
            "target_node": drone_id,
            "command_type": "UPDATE_TASK",
            "payload": {"task": "SCOUTING", "target_pos": [36.8065, 10.1815]},
            "from": "test-harness-skip-injector",
            "timestamp": time.time(),
        },
        1,
        False,
    )
    return {"drone_id": drone_id, "drone_trace_id": trace_id, "skip_injected_stage_c": True}
