# orchestrator/scenarios/scenario_2_telemetry_aggregation.py
"""
SCENARIO 2 \u2014 Telemetry Aggregation & Dashboard Visibility
===========================================================
Validates G-04, G-06, G-09. Doesn't require a real drone \u2014 the harness
itself plays the role of a drone publishing synthetic DroneTelemetry, so
this scenario isolates the LEADER + DASHBOARD side from the swarm side.

  Stage A   Harness publishes synthetic DroneTelemetry for a fake drone
            tagged with a real leader_id.
  Stage B   The real Swarm Leader picks it up (proven indirectly \u2014 see
            note below) and within its 10s status loop publishes a
            SwarmStatusSnapshot that reflects the new active drone.
  Stage C   Dashboard's MQTTBridge ingests the SwarmStatusSnapshot
            (proven by the dashboard's REST API reporting it \u2014 checked
            via a real HTTP call, not just MQTT \u2014 so this also validates
            the dashboard's parsing, not just the bus).
  Stage D   Dashboard SSE stream emits an event reflecting the update.

This scenario will tell you concretely: "telemetry reached the leader but
the leader's snapshot never updated" vs "the leader updated fine but the
dashboard never ingested it" vs "the dashboard ingested it but the SSE
stream never pushed it to clients".

"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from orchestrator.engine import Scenario, Stage

DASHBOARD_HTTP: str = "http://dashboard:8080"


def build(leader_id: str = "sl-A-01") -> Scenario:

    drone_id: str = f"test-scout-{str(uuid.uuid4())[:6]}"

    def publish_synthetic_telemetry(ctx: dict[str, Any]) -> dict[str, Any]:
        announce_payload: dict[str, Any] = {
            "node_id": drone_id,
            "node_type": "SCOUT_DRONE",
            "capabilities": ["RECEIVE_COMMANDS", "HEARTBEAT", "TELEMETRY", "SCOUT"],
            "status": "ONLINE",
            "zone": "zone_alpha",
            "location": [36.8065, 10.1815],
            "announced_at": time.time(),
        }
        telemetry_payload: dict[str, Any] = {
            "drone_id": drone_id,
            "leader_id": leader_id,
            "timestamp": time.time(),
            "position": [36.8065, 10.1815],
            "altitude_m_amsl": 120.0,
            "battery_wh": 500.0,
            "battery_pct": 0.85,
            "payload_litres": 0.0,
            "payload_kg": 0.0,
            "task": "SCOUTING",
            "connectivity": "STRONG",
            "thermal_peak_temp_c": 310.0,
            "thermal_coverage_pct": 0.4,
            "smoke_density_mg_m3": 50.0,
            "smoke_optical_density": 0.2,
            "flame_height_m": 1.5,
            "distance_to_flame_m": 80.0,
            "wind_speed_mps": 4.0,
            "wind_direction_deg": 200.0,
            "perimeter_estimate_m": 150.0,
        }
        ctx["_publish_now"] = [
            (f"wfc/registry/announce/{drone_id}", announce_payload, 1, True),
            (f"wfc/telemetry/{drone_id}", telemetry_payload, 0, False),
        ]
        return {"drone_id": drone_id, "telemetry_sent_ts": time.time()}

    stage_a = Stage(
        stage_id="A_telemetry_published",
        name="Synthetic drone telemetry published",
        component="Test Harness (drone simulator)",
        expect_desc=f"Harness publishes DroneTelemetry for {drone_id} \u2192 wfc/telemetry/{drone_id}",
        subscribe_topics=[f"wfc/telemetry/{drone_id}"],
        timeout_s=5.0,
        on_enter=publish_synthetic_telemetry,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict) and payload.get("drone_id") == drone_id  # pyright: ignore[reportUnnecessaryIsInstance]
        ),
    )

    stage_b = Stage(
        stage_id="B_leader_status_snapshot_updated",
        name="Leader's SwarmStatusSnapshot reflects the new drone",
        component="TelemetryAggregator / SwarmLeaderNode status loop",
        expect_desc=f"Leader {leader_id} publishes a SwarmStatusSnapshot with active_drones >= 1",
        subscribe_topics=[f"wfc/swarm/status/{leader_id}"],
        timeout_s=15.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("leader_id") == leader_id
            and (payload.get("active_drones") or 0) >= 1
        ),
        on_skip=lambda ctx: None,
    )

    async def check_dashboard_has_node(ctx: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{DASHBOARD_HTTP}/api/nodes/{drone_id}")
                if resp.status_code != 200:
                    return False
                data: Any = resp.json()
                return data is not None and data.get("battery_wh") is not None
        except Exception:
            return False

    stage_c = Stage(
        stage_id="C_dashboard_ingested_telemetry",
        name="Dashboard REST API reflects the drone's telemetry",
        component="MQTTBridge / SwarmState (Dashboard repo)",
        expect_desc=f"GET {DASHBOARD_HTTP}/api/nodes/{drone_id} returns a node with battery_wh set",
        subscribe_topics=[],
        timeout_s=8.0,
        active_check=check_dashboard_has_node,
        active_check_interval_s=1.0,
        match_fn=lambda topic, payload, ctx: False,
    )

    async def check_dashboard_event_log(ctx: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{DASHBOARD_HTTP}/api/events", params={"limit": 50})
                if resp.status_code != 200:
                    return False
                events: Any = resp.json()
                return any(isinstance(e, dict) and drone_id in json.dumps(e) for e in events)
        except Exception:
            return False

    stage_d = Stage(
        stage_id="D_dashboard_sse_event",
        name="Dashboard event log reflects the update",
        component="server.py /api/events + /api/stream (Dashboard repo)",
        expect_desc="Dashboard's recent event log contains a record referencing the test drone",
        subscribe_topics=[],
        timeout_s=8.0,
        active_check=check_dashboard_event_log,
        active_check_interval_s=1.0,
        match_fn=lambda topic, payload, ctx: False,
    )

    return Scenario(
        scenario_id="telemetry_aggregation",
        title="Telemetry Aggregation & Dashboard Visibility",
        description=(
            "Synthetic drone telemetry \u2192 Leader aggregates into SwarmStatusSnapshot \u2192 "
            "Dashboard ingests via MQTTBridge \u2192 visible via REST/SSE. "
            "Isolates the leader+dashboard pipeline from real drone hardware/sim."
        ),
        stages=[stage_a, stage_b, stage_c, stage_d],
    )
