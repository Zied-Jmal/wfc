from __future__ import annotations

import json
import time
import uuid
from typing import Any

import requests


class TestCommanderDashboardEvents:
    def test_dashboard_shows_cmd_events(
        self,
        mosquitto_broker: tuple[str, int],
        central_process: Any,
        swarm_leader_process: Any,
        dashboard_process: Any,
    ) -> None:
        import paho.mqtt.client as mqtt

        host, port = mosquitto_broker
        _dash_proc, dash_port = dashboard_process
        _leader_proc, _leader_id = swarm_leader_process
        fire_id: str = uuid.uuid4().hex[:8]

        pub = mqtt.Client()
        pub.connect(host, port)

        pub.publish(
            "wfc/events/fire",
            json.dumps(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "FIRE_DETECTED",
                    "timestamp": time.time(),
                    "source": "test-harness",
                    "payload": {
                        "fire_id": fire_id,
                        "zone": "zone_alpha",
                        "severity": "MEDIUM",
                        "sensor_id": "test-sensor",
                        "location_coords": [36.8065, 10.1815],
                    },
                }
            ),
            qos=1,
        )
        time.sleep(0.5)
        pub.disconnect()

        time.sleep(12)

        r = requests.get(f"http://127.0.0.1:{dash_port}/api/events?limit=50", timeout=5)

        assert r.status_code == 200, f"Dashboard returned {r.status_code}"
        events: Any = r.json()
        event_types: list[str] = [
            e.get("type")  # pyright: ignore[reportUnknownMemberType]
            for e in (events if isinstance(events, list) else events.get("events", []))  # pyright: ignore[reportUnknownVariableType]
        ]
        assert "ANNOUNCE" in event_types, f"no ANNOUNCE event: {event_types}"
        assert "ACK" in event_types or "CMD_SENT" in event_types, f"no ACK/CMD_SENT events. All types: {event_types}"
