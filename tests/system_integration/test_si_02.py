from __future__ import annotations

import json
import time
import uuid
from typing import Any

import requests


class TestSwarmDashboardTelemetry:
    def test_telemetry_flows_to_dashboard(
        self,
        mosquitto_broker: tuple[str, int],
        swarm_leader_process: Any,
        dashboard_process: Any,
    ) -> None:
        import paho.mqtt.client as mqtt

        host, port = mosquitto_broker
        _dash_proc, dash_port = dashboard_process
        _leader_proc, leader_id = swarm_leader_process
        drone_id: str = f"sd-si-{uuid.uuid4().hex[:6]}"

        pub = mqtt.Client()
        pub.connect(host, port)

        announcement: dict[str, Any] = {
            "node_id": drone_id,
            "node_type": "SCOUT_DRONE",
            "capabilities": ["SCOUT", "HEARTBEAT"],
            "status": "ONLINE",
            "zone": "zone_alpha",
            "location": [36.8070, 10.1825],
        }
        pub.publish(f"wfc/registry/announce/{drone_id}", json.dumps(announcement), qos=1, retain=True)
        time.sleep(1)

        telemetry: dict[str, Any] = {
            "drone_id": drone_id,
            "leader_id": leader_id,
            "timestamp": time.time(),
            "position": [36.8070, 10.1825],
            "altitude_m_amsl": 100.0,
            "battery_wh": 500.0,
            "battery_pct": 0.85,
            "payload_litres": 0.0,
            "payload_kg": 0.0,
            "task": "SCOUTING",
            "connectivity": "STRONG",
            "thermal_peak_temp_c": 300.0,
        }
        pub.publish(f"wfc/telemetry/{drone_id}", json.dumps(telemetry), qos=1)
        pub.disconnect()

        time.sleep(5)

        r = requests.get(f"http://127.0.0.1:{dash_port}/api/nodes/{drone_id}", timeout=5)

        assert r.status_code == 200, f"Dashboard returned {r.status_code}"
        data: Any = r.json()
        assert data.get("node_id") == drone_id, f"node_id mismatch: {data}"
        assert data.get("battery_pct", 0) > 0, f"telemetry not ingested: {data}"
