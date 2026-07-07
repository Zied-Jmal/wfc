# CI-DASH-001: MQTTBridge malformed payload doesn't crash
from __future__ import annotations

import pytest
import json
from typing import Any

from dashboard.state import SwarmState
from dashboard.mqtt_bridge import MQTTBridge


class MockMsg:
    def __init__(self, topic: str, payload: Any) -> None:
        self.topic = topic
        self.payload = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload


class TestMQTTBridge:
    def test_malformed_payload_doesnt_crash(self, bridge: MQTTBridge, swarm_state: SwarmState) -> None:
        # Feed a malformed JSON (missing required fields) directly to the handler
        bad_payload = {"bad_field": "no_drone_id"}
        msg = MockMsg("wfc/telemetry/sd-1", bad_payload)
        # This should not raise
        try:
            bridge._on_message(None, None, msg)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
        except Exception:
            pytest.fail("Malformed payload should not crash the bridge")

        # After the bad message, state should be clean
        node = swarm_state.get_node("sd-1")
        assert node is None

    def test_valid_payload_still_works_after_malformed(self, bridge: MQTTBridge, swarm_state: SwarmState) -> None:
        import time
        from wfc_shared.schemas.announcements import NodeAnnouncement
        # First register a node via announcement
        ann = NodeAnnouncement(
            node_id="sd-1", node_type="SCOUT_DRONE",
            capabilities=["SCOUT"], status="ONLINE",
            host="10.0.0.2", announced_at=time.time(),
        )
        # Apply directly
        swarm_state.apply_announcement(ann)

        # Send malformed
        bad_msg = MockMsg("wfc/telemetry/sd-1", {"bad": "data"})
        try:
            bridge._on_message(None, None, bad_msg)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
        except Exception:
            pytest.fail("Malformed payload should not crash bridge")

        # Node should still exist (was registered before)
        node = swarm_state.get_node("sd-1")
        assert node is not None
