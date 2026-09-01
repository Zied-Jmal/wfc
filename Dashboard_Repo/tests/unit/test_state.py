# TEST: U-DASH-001, U-DASH-002
from __future__ import annotations

import time

from dashboard.state import SwarmState
from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.telemetry import DroneTelemetry


class TestSwarmState:
    def test_apply_telemetry_noop_for_unregistered_node(
        self, swarm_state: SwarmState, drone_telemetry: DroneTelemetry
    ) -> None:
        swarm_state.apply_telemetry(drone_telemetry)
        node = swarm_state.get_node("sd-1")
        assert node is None

    def test_announcement_overwrites_offline_status(
        self, swarm_state: SwarmState, announcement: NodeAnnouncement
    ) -> None:
        # First announce as ONLINE
        swarm_state.apply_announcement(announcement)
        node = swarm_state.get_node("sl-1")
        assert node is not None
        assert node["status"] == "ONLINE"

        # Now announce as OFFLINE
        offline_ann = NodeAnnouncement(
            node_id="sl-1",
            node_type=announcement.node_type,
            capabilities=announcement.capabilities,
            status="OFFLINE",
            host="10.0.0.1",
            announced_at=time.time(),
            zone="zone_alpha",
            location=(36.8, 10.18),
        )
        swarm_state.apply_announcement(offline_ann)
        node = swarm_state.get_node("sl-1")
        assert node["status"] == "OFFLINE"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]

    def test_announcement_creates_node(self, swarm_state: SwarmState, announcement: NodeAnnouncement) -> None:
        swarm_state.apply_announcement(announcement)
        node = swarm_state.get_node("sl-1")
        assert node is not None
        assert node["node_id"] == "sl-1"
        assert node["node_type"] == "SWARM_LEADER"
