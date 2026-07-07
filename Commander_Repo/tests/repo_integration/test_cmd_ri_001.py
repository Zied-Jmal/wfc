# RI-CMD-001: Commander central node publishes retained ONLINE announcement on startup

from __future__ import annotations
from typing import Any

import threading, json, time  # pyright: ignore[reportUnusedImport]

class TestCommanderStartupAnnouncement:
    def test_retained_online_announcement_on_startup(self, mosquitto_broker: Any, env_setup: Any, tmp_path: Any) -> None:
        host, port = mosquitto_broker
        import paho.mqtt.client as mqtt

        announced = threading.Event()
        result = {}

        def on_msg(c: Any, u: Any, m: Any) -> None:
            try:
                payload = json.loads(m.payload)
                if payload.get("status") == "ONLINE":
                    result["topic"] = m.topic
                    result["payload"] = payload
                    announced.set()
            except Exception:
                pass

        sub = mqtt.Client()
        sub.on_message = on_msg
        sub.connect(host, port)
        sub.loop_start()

        from command_nodes.central.services.node_runtime import CentralNode
        node = CentralNode()
        node.start()

        # Subscribe AFTER node starts to avoid retained OFFLINE from previous tests
        sub.subscribe("wfc/registry/announce/central-commander", qos=1)

        ok = announced.wait(timeout=10)
        node.stop()
        sub.loop_stop()
        sub.disconnect()

        assert ok, "No ONLINE announcement received within 10s"
        assert result["payload"].get("status") == "ONLINE"  # pyright: ignore[reportUnknownMemberType]
        assert result["payload"].get("node_id") == "central-commander"  # pyright: ignore[reportUnknownMemberType]
