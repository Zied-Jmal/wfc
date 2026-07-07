# RI-CMD-002: HIGH fire ESCALATE_FIRE held for approval, observable on wire

from __future__ import annotations
from typing import Any

import threading, json, time, uuid

class TestHighFireHeldForApproval:
    def test_high_fire_held_not_auto_dispatched(self, mosquitto_broker: Any, env_setup: Any, tmp_path: Any) -> None:
        host, port = mosquitto_broker
        import paho.mqtt.client as mqtt

        pending_received = threading.Event()
        cmd_received = threading.Event()  # pyright: ignore[reportUnusedVariable]
        pending_msg = {}
        cmd_msgs = []

        def on_msg(c: Any, u: Any, m: Any) -> None:
            topic = m.topic
            try:
                payload = json.loads(m.payload)
            except Exception:
                return
            if topic == "wfc/approval/pending":
                pending_msg["data"] = payload
                pending_received.set()
            elif topic.startswith("wfc/command/"):
                cmd_msgs.append(payload)  # pyright: ignore[reportUnknownMemberType]

        sub = mqtt.Client()
        sub.on_message = on_msg
        sub.connect(host, port)
        sub.subscribe("wfc/approval/pending", qos=1)
        sub.subscribe("wfc/command/+", qos=1)
        sub.loop_start()

        from command_nodes.central.services.node_runtime import CentralNode
        node = CentralNode()
        node.start()
        time.sleep(1)

        pub = mqtt.Client()
        pub.connect(host, port)

        # Wait for central-commander (which has DISPATCH_COMMANDS)
        # to reach ACTIVE status via its own heartbeat cycle
        from wfc_shared.enums.node_status import ACTIVE as STATUS_ACTIVE
        for _ in range(50):
            cc = node._core._registry.get("central-commander")  # pyright: ignore[reportPrivateUsage]
            if cc and cc.status == STATUS_ACTIVE:
                break
            time.sleep(0.2)

        fire_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "FIRE_DETECTED",
            "timestamp": time.time(),
            "source": "test-harness",
            "payload": {
                "fire_id": str(uuid.uuid4())[:8],
                "zone": "zone_alpha",
                "severity": "HIGH",
                "sensor_id": "test-sensor",
                "location_coords": [36.8065, 10.1815],
            },
        }
        pub.publish("wfc/events/fire", json.dumps(fire_event), qos=1)
        time.sleep(0.5)
        pub.disconnect()

        ok = pending_received.wait(timeout=15)
        node.stop()
        sub.loop_stop()
        sub.disconnect()

        assert ok, "No COMMAND_PENDING on wfc/approval/pending within 15s"
        assert pending_msg["data"].get("event") == "COMMAND_PENDING"  # pyright: ignore[reportUnknownMemberType]
        assert any(
            m.get("command_type") == "ESCALATE_FIRE" for m in cmd_msgs  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType, reportUnknownVariableType]
        ) is False, "ESCALATE_FIRE was dispatched directly (should be held)"
