"""Tests for wfc_shared schemas — validates all Pydantic models against sample payloads."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.commands import Command
from wfc_shared.schemas.domain_event import DomainEvent
from wfc_shared.schemas.events import FireEvent, FirePayload
from wfc_shared.schemas.pending import PendingCommand
from wfc_shared.schemas.telemetry import DroneTelemetry, FireIntensityUpdate, SwarmStatusSnapshot


class TestCommand:
    def test_minimal_command(self) -> None:
        cmd = Command(target_node="sl-1", command_type="RESPOND_TO_FIRE")
        assert cmd.target_node == "sl-1"
        assert cmd.command_type == "RESPOND_TO_FIRE"
        assert cmd.command_id  # auto-generated
        assert cmd.trace_id  # auto-generated
        assert cmd.timestamp > 0

    def test_command_with_payload(self) -> None:
        cmd = Command(
            target_node="fd-1",
            command_type="DISPATCH_DRONE",
            payload={"task": "SCOUTING", "fire_id": "f1"},
        )
        assert cmd.payload["task"] == "SCOUTING"

    def test_command_rejects_invalid_type(self) -> None:
        with pytest.raises(ValidationError):
            Command(target_node="sl-1", command_type="INVALID")  # type: ignore[arg-type]


class TestNodeAnnouncement:
    def test_minimal_announcement(self) -> None:
        ann = NodeAnnouncement(
            node_id="sl-A-01",
            node_type="SWARM_LEADER",
            capabilities=["SWARM_LEAD"],
        )
        assert ann.node_id == "sl-A-01"
        assert ann.status == "ONLINE"
        assert ann.zone is None

    def test_full_announcement(self) -> None:
        ann = NodeAnnouncement(
            node_id="sd-01",
            node_type="SCOUT_DRONE",
            capabilities=["SCOUT"],
            status="ONLINE",
            host="10.0.0.1",
            zone="zone_alpha",
            location=(36.8065, 10.1815),
        )
        assert ann.location == (36.8065, 10.1815)


class TestFirePayload:
    def test_minimal_payload(self) -> None:
        fp = FirePayload(fire_id="f1", zone="zone_alpha", severity="HIGH", sensor_id="s1")
        assert fp.fire_id == "f1"
        assert fp.location_coords is None

    def test_location_to_zone_compat(self) -> None:
        fp = FirePayload(fire_id="f1", severity="HIGH", sensor_id="s1", location="zone_bravo")  # pyright: ignore[reportCallIssue] (back-compat: location is copied to zone by validator)
        assert fp.zone == "zone_bravo"


class TestFireEvent:
    def test_create_event(self) -> None:
        ev = FireEvent(
            event_type="FIRE_DETECTED",
            source="sensor-01",
            payload=FirePayload(fire_id="f1", zone="zone_alpha", severity="HIGH", sensor_id="s1"),
        )
        assert ev.event_type == "FIRE_DETECTED"
        assert ev.payload.fire_id == "f1"


class TestDroneTelemetry:
    def test_scout_telemetry(self) -> None:
        t = DroneTelemetry(
            drone_id="sd-1",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.80, 10.18),
            altitude_m_amsl=100.0,
            battery_wh=500.0,
            battery_pct=0.85,
            task="SCOUTING",
            thermal_peak_temp_c=300.0,
        )
        assert t.drone_id == "sd-1"
        assert t.task == "SCOUTING"

    def test_fighter_telemetry(self) -> None:
        t = DroneTelemetry(
            drone_id="fd-1",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.81, 10.19),
            altitude_m_amsl=80.0,
            battery_wh=400.0,
            battery_pct=0.75,
            payload_litres=50.0,
            payload_kg=50.0,
            task="SUPPRESSING",
            drop_passes=2,
            pump_active=True,
        )
        assert t.drop_passes == 2
        assert t.pump_active is True


class TestSwarmStatusSnapshot:
    def test_create_snapshot(self) -> None:
        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="f1",
            timestamp=time.time(),
            active_drones=3,
            avg_battery_pct=0.8,
        )
        assert snap.active_drones == 3
        assert snap.status == "IDLE"


class TestFireIntensityUpdate:
    def test_create_update(self) -> None:
        update = FireIntensityUpdate(
            fire_id="f1",
            leader_id="sl-1",
            timestamp=time.time(),
            new_intensity="HIGH",
            perimeter_m=150.0,
            spread_rate="RAPID",
        )
        assert update.new_intensity == "HIGH"


class TestPendingCommand:
    def test_create_pending(self) -> None:
        cmd = Command(target_node="sl-1", command_type="ESCALATE_FIRE")
        pc = PendingCommand(command=cmd)
        assert pc.status == "PENDING"
        assert pc.pending_id  # auto-generated


class TestDomainEvent:
    def test_create_event(self) -> None:
        ev = DomainEvent(
            event_type="FIRE_DETECTED",
            fire_id="f1",
            reason="sensor detected fire",
        )
        assert ev.event_type == "FIRE_DETECTED"
        assert ev.source == "commander"
        assert ev.replayed is False
