# RI-SWARM-001: Swarm leader dispatches drone command on receiving RESPOND_TO_FIRE

import json
import threading
import time
import uuid
from typing import Any


class TestSwarmLeaderDispatch:
    def test_dispatched_command_reaches_drone_topic(
        self, mosquitto_broker: tuple[str, int], env_setup: Any, tmp_path: Any
    ) -> None:
        host, port = mosquitto_broker  # pyright: ignore[reportUnknownVariableType]
        import paho.mqtt.client as mqtt

        drone_cmd_received = threading.Event()
        drone_cmd = {}

        def on_msg(c: mqtt.Client, u: Any, m: mqtt.MQTTMessage) -> None:
            if m.topic.startswith("wfc/command/") and "sl-" not in m.topic:
                try:
                    drone_cmd["topic"] = m.topic
                    drone_cmd["payload"] = json.loads(m.payload)
                    drone_cmd_received.set()
                except Exception:
                    pass

        sub = mqtt.Client()
        sub.on_message = on_msg
        sub.connect(host, port)  # pyright: ignore[reportUnknownArgumentType]
        sub.subscribe("wfc/command/+", qos=1)
        sub.loop_start()

        from core.node.swarm_leader_node import SwarmLeaderNode

        leader = SwarmLeaderNode(
            node_id="sl-test-01",
            zone="zone_alpha",
            location=(36.8065, 10.1815),
            backup_peers=[],
            is_backup=False,
        )
        leader.start()
        time.sleep(1.5)

        pub = mqtt.Client()
        pub.connect(host, port)  # pyright: ignore[reportUnknownArgumentType]

        drone_id = "sd-test-99"
        announcement = {
            "node_id": drone_id,
            "node_type": "SCOUT_DRONE",
            "capabilities": ["SCOUT", "HEARTBEAT"],
            "status": "ONLINE",
            "zone": "zone_alpha",
            "location": [36.8070, 10.1825],
        }
        pub.publish(f"wfc/registry/announce/{drone_id}", json.dumps(announcement), qos=1, retain=True)
        time.sleep(1)

        cmd = {
            "trace_id": str(uuid.uuid4()),
            "target_node": "sl-test-01",
            "command_type": "RESPOND_TO_FIRE",
            "payload": {
                "fire_id": str(uuid.uuid4())[:8],
                "zone": "zone_alpha",
                "location": [36.8065, 10.1815],
                "severity": "MEDIUM",
                "sensor_id": "test-sensor",
            },
            "from": "test-harness",
            "timestamp": time.time(),
        }
        pub.publish("wfc/command/sl-test-01", json.dumps(cmd), qos=1)
        time.sleep(0.5)
        pub.disconnect()

        ok = drone_cmd_received.wait(timeout=15)
        leader.stop()
        sub.loop_stop()
        sub.disconnect()

        if not ok:
            print(f"[DEBUG] drone_cmd received: {drone_cmd}")

        assert ok, "No drone command published to wfc/command/+ within 15s"
        ct: str = drone_cmd["payload"].get("command_type", "")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        assert ct in ("DISPATCH_DRONE", "UPDATE_TASK", "RECALL_DRONE"), f"Expected drone command type, got {ct}"
