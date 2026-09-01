"""
SCENARIO 5 \u2014 Node Lifecycle, Heartbeat & LWT Crash Detection
================================================================
Validates G-02 from PROJECT_MAP.md. Spins up a REAL throwaway MQTT client
(acting as a fake field node) and proves the system reacts correctly to
it joining AND abruptly dying (via LWT, not a clean disconnect).

  Stage A   Harness connects a throwaway client (fake-node-{uuid}) WITH
            an LWT registered BEFORE connecting, and publishes a retained
            ONLINE announcement, exactly as BaseNode.start() does.
  Stage B   Dashboard reflects the fake node as ONLINE.
  Stage C   Harness publishes 2 heartbeats on schedule, then ABRUPTLY
            closes the raw TCP socket without calling disconnect() \u2014
            simulating a real crash (kill -9), not a graceful shutdown.
  Stage D   The broker's LWT mechanism fires \u2014 confirmed by observing the
            OFFLINE payload appear WITHOUT the harness explicitly publishing it.
  Stage E   Dashboard reflects the node as OFFLINE within the documented
            timeout window.

This is the scenario most likely to catch silent regressions in LWT wiring
(e.g. if someone moves client.connect() before will_set(), the broker
never receives the will and Stage D will time out specifically).

"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from typing import Any

import httpx
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from orchestrator.engine import Scenario, Stage

DASHBOARD_HTTP: str = "http://dashboard:8080"
MQTT_HOST: str = "mosquitto"
MQTT_PORT: int = 1883


def build() -> Scenario:

    fake_node_id: str = f"fake-node-{str(uuid.uuid4())[:6]}"
    _state: dict[str, Any] = {"client": None}

    def connect_with_lwt(ctx: dict[str, Any]) -> dict[str, Any]:
        client = mqtt.Client(
            client_id=fake_node_id,
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        offline_payload: str = json.dumps(
            {
                "node_id": fake_node_id,
                "node_type": "SCOUT_DRONE",
                "capabilities": [],
                "status": "OFFLINE",
                "announced_at": time.time(),
            }
        )
        client.will_set(
            f"wfc/registry/announce/{fake_node_id}",
            offline_payload,
            qos=1,
            retain=True,
        )
        client.connect(MQTT_HOST, MQTT_PORT)
        client.loop_start()
        _state["client"] = client

        online_payload: dict[str, Any] = {
            "node_id": fake_node_id,
            "node_type": "SCOUT_DRONE",
            "capabilities": ["RECEIVE_COMMANDS", "HEARTBEAT", "TELEMETRY", "SCOUT"],
            "status": "ONLINE",
            "announced_at": time.time(),
            "zone": "zone_alpha",
            "location": [36.8065, 10.1815],
        }
        client.publish(
            f"wfc/registry/announce/{fake_node_id}",
            json.dumps(online_payload),
            qos=1,
            retain=True,
        )
        return {"fake_node_id": fake_node_id, "connected_at": time.time()}

    stage_a = Stage(
        stage_id="A_fake_node_online",
        name="Fake node connects and announces ONLINE",
        component="Test Harness (fake field node)",
        expect_desc="Retained ONLINE announcement observed on the broker for the fake node",
        subscribe_topics=[f"wfc/registry/announce/{fake_node_id}"],
        timeout_s=8.0,
        on_enter=connect_with_lwt,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == fake_node_id
            and payload.get("status") == "ONLINE"
        ),
    )

    async def check_dashboard_sees_node(ctx: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{DASHBOARD_HTTP}/api/nodes/{fake_node_id}")
                if resp.status_code != 200:
                    return False
                data: Any = resp.json()
                return data is not None and data.get("status") == "ONLINE"
        except Exception:
            return False

    stage_b = Stage(
        stage_id="B_dashboard_confirms_online",
        name="Dashboard reflects the fake node as ONLINE",
        component="MQTTBridge / SwarmState (Dashboard repo)",
        expect_desc=f"GET {DASHBOARD_HTTP}/api/nodes/{{id}} returns status=ONLINE",
        subscribe_topics=[],
        timeout_s=8.0,
        active_check=check_dashboard_sees_node,
        active_check_interval_s=1.0,
        match_fn=lambda topic, payload, ctx: False,
    )

    def send_heartbeats_then_kill(ctx: dict[str, Any]) -> dict[str, Any]:
        client: Any = _state["client"]
        if client is None:
            return {"heartbeat_error": "no client from stage A"}

        def hb_payload() -> str:
            return json.dumps(
                {
                    "node_id": fake_node_id,
                    "type": "SCOUT_DRONE",
                    "timestamp": time.time(),
                    "status": "alive",
                }
            )

        client.publish(f"wfc/nodes/{fake_node_id}/heartbeat", hb_payload(), qos=0)

        def _delayed_kill() -> None:
            time.sleep(2.0)
            client.publish(f"wfc/nodes/{fake_node_id}/heartbeat", hb_payload(), qos=0)
            time.sleep(1.0)
            try:
                client._sock_close()
            except Exception:
                with contextlib.suppress(Exception):
                    client.socket().close()

        threading.Thread(target=_delayed_kill, daemon=True).start()
        return {"heartbeats_sent_at": time.time(), "kill_scheduled": True}

    stage_c = Stage(
        stage_id="C_heartbeats_then_ungraceful_kill",
        name="Heartbeats sent, then connection killed WITHOUT graceful disconnect",
        component="Test Harness (crash simulator)",
        expect_desc="Two heartbeats observed, then the raw socket is force-closed (simulated kill -9)",
        subscribe_topics=[f"wfc/nodes/{fake_node_id}/heartbeat"],
        timeout_s=8.0,
        on_enter=send_heartbeats_then_kill,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict) and payload.get("node_id") == fake_node_id  # pyright: ignore[reportUnnecessaryIsInstance]
        ),
    )

    stage_d = Stage(
        stage_id="D_broker_fires_lwt",
        name="Broker's LWT fires \u2014 OFFLINE announcement appears without explicit publish",
        component="Mosquitto broker LWT mechanism",
        expect_desc="Registry announce updates to status=OFFLINE after the kill",
        subscribe_topics=[f"wfc/registry/announce/{fake_node_id}"],
        timeout_s=15.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == fake_node_id
            and payload.get("status") == "OFFLINE"
        ),
        on_skip=lambda ctx: None,
    )

    async def check_dashboard_sees_offline(ctx: dict[str, Any]) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{DASHBOARD_HTTP}/api/nodes/{fake_node_id}")
                if resp.status_code != 200:
                    return False
                data: Any = resp.json()
                return data is not None and data.get("status") == "OFFLINE"
        except Exception:
            return False

    stage_e = Stage(
        stage_id="E_dashboard_confirms_offline",
        name="Dashboard reflects the crash within the documented timeout",
        component="MQTTBridge / SwarmState (Dashboard repo) \u2014 validates G-02",
        expect_desc="Dashboard API eventually returns status=OFFLINE for the fake node",
        subscribe_topics=[],
        timeout_s=12.0,
        active_check=check_dashboard_sees_offline,
        active_check_interval_s=1.0,
        match_fn=lambda topic, payload, ctx: False,
    )

    return Scenario(
        scenario_id="node_lifecycle",
        title="Node Lifecycle, Heartbeat & LWT Crash Detection",
        description=(
            "Connects a throwaway fake node exactly as BaseNode does (LWT before "
            "connect), proves ONLINE announce + heartbeats propagate to the "
            "dashboard, then simulates an ungraceful crash and verifies the "
            "broker's Last Will fires and the dashboard reflects OFFLINE within "
            "the documented timeout. Validates G-02."
        ),
        stages=[stage_a, stage_b, stage_c, stage_d, stage_e],
    )
