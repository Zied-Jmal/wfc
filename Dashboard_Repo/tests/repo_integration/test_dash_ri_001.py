# RI-DASH-001: Dashboard approval_respond endpoint publishes correct wire-format decision (regression test)
from __future__ import annotations

import threading
import json
import time
import requests
from typing import Any


class TestDashboardApprovalWireFormat:
    def test_approval_respond_publishes_decision_string(self, mosquitto_broker: tuple[str, int], env_setup: None, tmp_path: Any) -> None:
        host, port = mosquitto_broker
        import paho.mqtt.client as mqtt

        response_received = threading.Event()
        response_msg: dict[str, Any] = {}

        def on_msg(c: mqtt.Client, u: Any, m: mqtt.MQTTMessage) -> None:
            if m.topic == "wfc/approval/response":
                try:
                    response_msg["data"] = json.loads(m.payload)
                    response_received.set()
                except Exception:
                    pass

        sub = mqtt.Client()
        sub.on_message = on_msg
        sub.connect(host, port)
        sub.subscribe("wfc/approval/response", qos=1)
        sub.loop_start()

        import uvicorn
        from dashboard.server import app

        dash_thread = threading.Thread(
            target=lambda: uvicorn.run(app, host="127.0.0.1", port=8080, log_level="error"),
            daemon=True,
        )
        dash_thread.start()
        time.sleep(2)

        resp = requests.post(
            "http://127.0.0.1:8080/api/approval/respond",
            json={"request_id": "p1", "approved": True},
            timeout=5,
        )

        ok = response_received.wait(timeout=10)

        sub.loop_stop()
        sub.disconnect()

        assert ok, "No response on wfc/approval/response within 10s"
        data = response_msg["data"]
        assert "decision" in data, f"Payload missing 'decision' key: {data}"
        assert "approved" not in data, f"Payload contains 'approved' bool: {data}"
        assert data["decision"] == "APPROVED", f"Expected decision=APPROVED, got {data['decision']}"
        assert resp.status_code == 200
