from __future__ import annotations

import time

import pytest

from dashboard.state import SwarmState
from wfc_shared.enums.node_types import SWARM_LEADER
from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.telemetry import DroneTelemetry


@pytest.fixture
def swarm_state() -> SwarmState:
    from dashboard.state import SwarmState

    return SwarmState()


@pytest.fixture
def announcement() -> NodeAnnouncement:
    return NodeAnnouncement(
        node_id="sl-1",
        node_type=SWARM_LEADER,  # pyright: ignore[reportArgumentType]
        capabilities=["SWARM_LEAD"],
        status="ONLINE",
        host="10.0.0.1",
        announced_at=time.time(),
        zone="zone_alpha",
        location=(36.8, 10.18),
    )


@pytest.fixture
def drone_telemetry() -> DroneTelemetry:
    return DroneTelemetry(
        drone_id="sd-1",
        leader_id="sl-1",
        timestamp=time.time(),
        position=(36.81, 10.19),
        altitude_m_amsl=100.0,
        battery_wh=200.0,
        battery_pct=0.85,
        payload_litres=0.0,
        payload_kg=0.0,
        task="SCOUTING",
        connectivity="STRONG",
        thermal_peak_temp_c=300.0,
        thermal_coverage_pct=0.75,
        smoke_density_mg_m3=50.0,
        smoke_optical_density=0.3,
        flame_height_m=5.0,
        distance_to_flame_m=100.0,
        perimeter_estimate_m=150.0,
        wind_speed_mps=5.0,
        wind_direction_deg=225.0,
    )
