# RI-SWARM-002: Heartbeat timeout / OFFLINE announcement DroneRegistry unregister (regression test)

import json
import threading
import time
import uuid
from typing import Any


class TestSwarmLeaderOfflineDrone:
    def test_offline_drone_not_assigned(self, mosquitto_broker: tuple[str, int], env_setup: Any, tmp_path: Any) -> None:
        host, port = mosquitto_broker  # pyright: ignore[reportUnknownVariableType]
        import paho.mqtt.client as mqtt

        drone_cmd_received = threading.Event()
        drone_cmds: list[Any] = []
        lock = threading.Lock()

        def on_msg(c: mqtt.Client, u: Any, m: mqtt.MQTTMessage) -> None:
            if m.topic.startswith("wfc/command/") and "sl-" not in m.topic:
                try:
                    with lock:  # pyright: ignore[reportUnknownMemberType]
                        drone_cmds.append(
                            {
                                "topic": m.topic,
                                "payload": json.loads(m.payload),
                            }
                        )
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
            node_id="sl-test-02",
            zone="zone_alpha",
            location=(36.8065, 10.1815),
            backup_peers=[],
            is_backup=False,
        )
        leader.start()
        time.sleep(1.5)

        pub = mqtt.Client()
        pub.connect(host, port)  # pyright: ignore[reportUnknownArgumentType]

        drone_id = "sd-test-offline"
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

        offline_announcement = {
            "node_id": drone_id,
            "node_type": "SCOUT_DRONE",
            "capabilities": ["SCOUT", "HEARTBEAT"],
            "status": "OFFLINE",
            "zone": "zone_alpha",
            "location": [36.8070, 10.1825],
        }
        pub.publish(f"wfc/registry/announce/{drone_id}", json.dumps(offline_announcement), qos=1, retain=True)
        time.sleep(1)

        cmd = {
            "trace_id": str(uuid.uuid4()),
            "target_node": "sl-test-02",
            "command_type": "RESPOND_TO_FIRE",
            "payload": {
                "fire_id": str(uuid.uuid4())[:8],
                "zone": "zone_alpha",
                "location": "zone_alpha",
                "location_coords": [36.8065, 10.1815],
                "severity": "MEDIUM",
                "sensor_id": "test-sensor",
            },
            "from": "test-harness",
            "timestamp": time.time(),
        }
        pub.publish("wfc/command/sl-test-02", json.dumps(cmd), qos=1)
        time.sleep(0.5)
        pub.disconnect()

        drone_cmd_received.wait(timeout=5)

        leader.stop()
        sub.loop_stop()
        sub.disconnect()

        with lock:
            dead_drone_commands = [m for m in drone_cmds if drone_id in m["topic"]]  # pyright: ignore[reportUnknownVariableType]
        assert len(dead_drone_commands) == 0, (  # pyright: ignore[reportUnknownArgumentType]
            f"OFFLINE drone {drone_id} received {len(dead_drone_commands)} command(s) - should be 0"
        )  # pyright: ignore[reportUnknownArgumentType]
