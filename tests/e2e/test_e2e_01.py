from __future__ import annotations

import json
import time
import uuid
from threading import Event
from typing import Any

import paho.mqtt.client as mqtt


class TestFireDispatchEndToEnd:

    def test_full_fire_dispatch_flow(
        self,
        mosquitto_broker: tuple[str, int],
        mqtt_client: tuple[mqtt.Client, list[tuple[str, Any]], Event],
        central_process: Any,
        swarm_leader_process: Any,
    ) -> None:
        host, port = mosquitto_broker
        sub, all_msgs, msg_ev = mqtt_client
        leader_proc, leader_id = swarm_leader_process
        fire_id: str = uuid.uuid4().hex[:8]

        sub.subscribe(f"wfc/command/{leader_id}", qos=1)
        sub.subscribe("wfc/ack", qos=1)
        time.sleep(1)

        pub = mqtt.Client()
        pub.connect(host, port)
        pub.publish("wfc/events/fire", json.dumps({
            "event_id": str(uuid.uuid4()), "event_type": "FIRE_DETECTED",
            "timestamp": time.time(), "source": "test-harness",
            "payload": {
                "fire_id": fire_id, "zone": "zone_alpha",
                "severity": "MEDIUM", "sensor_id": "test-sensor",
                "location_coords": [36.8065, 10.1815],
            },
        }), qos=1)
        time.sleep(0.5)
        pub.disconnect()

        time.sleep(15)

        cmd_msgs: list[tuple[str, Any]] = [(t, p) for t, p in all_msgs if t == f"wfc/command/{leader_id}"]
        ack_msgs: list[tuple[str, Any]] = [(t, p) for t, p in all_msgs if t == "wfc/ack"]

        assert len(cmd_msgs) >= 1, f"No RESPOND_TO_FIRE command. Topics: {[t for t,_ in all_msgs]}"
        assert cmd_msgs[0][1].get("command_type") == "RESPOND_TO_FIRE"

        leader_acks: list[Any] = [p for _, p in ack_msgs if p.get("node_id") == leader_id]
        assert any(p.get("status") == "RECEIVED" for p in leader_acks), \
            f"No RECEIVED ACK from leader. Leader acks: {leader_acks}"
