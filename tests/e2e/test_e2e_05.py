from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from threading import Event
from typing import Any

import paho.mqtt.client as mqtt


class TestNodeLifecycleLWT:
    def test_graceful_shutdown_publishes_offline(
        self,
        mosquitto_broker: tuple[str, int],
        mqtt_client: tuple[mqtt.Client, list[tuple[str, Any]], Event],
    ) -> None:
        host, port = mosquitto_broker
        sub, all_msgs, msg_ev = mqtt_client
        root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        swarm: str = os.path.join(root, "Swarm_Repo")
        shared: str = os.path.join(root, "wfc_shared")
        node_id: str = f"lifecycle-{uuid.uuid4().hex[:6]}"

        env: dict[str, str] = {
            **os.environ,
            "MQTT_HOST": host,
            "MQTT_PORT": str(port),
            "NODE_ID": node_id,
            "NODE_ZONE": "zone_alpha",
            "NODE_LOCATION": "36.8065,10.1815",
            "IS_BACKUP": "false",
            "DEBUG": "0",
            "PYTHONPATH": f"{swarm};{shared}",
        }
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, "main_leader.py"], cwd=swarm, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(3)

        sub.subscribe(f"wfc/registry/announce/{node_id}", qos=1)
        time.sleep(2)

        online_msgs_before: list[tuple[str, Any]] = [
            (t, p) for t, p in all_msgs if t == f"wfc/registry/announce/{node_id}" and p.get("status") == "ONLINE"
        ]
        assert len(online_msgs_before) >= 1, (
            f"Node {node_id} never announced ONLINE. Topics: {[t for t, _ in all_msgs]}"
        )

        all_msgs.clear()
        msg_ev.clear()

        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()

        time.sleep(3)

        offline_msgs: list[tuple[str, Any]] = [
            (t, p) for t, p in all_msgs if t == f"wfc/registry/announce/{node_id}" and p.get("status") == "OFFLINE"
        ]

        assert len(offline_msgs) >= 1, (
            f"Node {node_id} did not publish OFFLINE on shutdown. Messages after kill: {all_msgs}"
        )
