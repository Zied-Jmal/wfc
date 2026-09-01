# TEST: U-TACT-001 to U-TACT-003

import time

from core.state.drone_registry import DroneRecord
from wfc_shared.schemas.telemetry import DroneTelemetry, SwarmStatusSnapshot


class TestFireTactics:
    def test_assign_respond_to_fire_skips_low_payload(self):
        from core.tactics.fire_tactics import FireTactics

        tactics = FireTactics()

        scout = DroneRecord(drone_id="sd-1", role="SCOUT", location=(36.8, 10.18))
        fighter_ok = DroneRecord(drone_id="fd-1", role="FIREFIGHTING", location=(36.81, 10.19))
        fighter_ok.last_telemetry = DroneTelemetry(
            drone_id="fd-1",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.81, 10.19),
            battery_wh=200.0,
            battery_pct=0.85,
            payload_litres=8.0,
            task="IDLE",
            connectivity="STRONG",
        )
        fighter_low = DroneRecord(drone_id="fd-2", role="FIREFIGHTING", location=(36.82, 10.20))
        fighter_low.last_telemetry = DroneTelemetry(
            drone_id="fd-2",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.82, 10.20),
            battery_wh=200.0,
            battery_pct=0.85,
            payload_litres=1.0,
            task="IDLE",
            connectivity="STRONG",
        )

        assignments = tactics.assign_respond_to_fire(
            "fire-1", (36.80, 10.18), "HIGH", [scout], [fighter_ok, fighter_low]
        )
        assigned_ids = [a.drone_id for a in assignments]
        assert "fd-1" in assigned_ids
        assert "fd-2" not in assigned_ids

    def test_reassess_low_battery_triggers_returning(self):
        from core.tactics.fire_tactics import FireTactics

        tactics = FireTactics()

        from core.state.drone_registry import DroneRegistry

        reg = DroneRegistry()

        low_batt_telem = DroneTelemetry(
            drone_id="fd-1",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.8, 10.18),
            battery_wh=50.0,
            battery_pct=0.20,
            payload_litres=8.0,
            task="SUPPRESSING",
            connectivity="STRONG",
        )

        fresh_telem = DroneTelemetry(
            drone_id="fd-2",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.82, 10.20),
            battery_wh=200.0,
            battery_pct=0.90,
            payload_litres=10.0,
            task="IDLE",
            connectivity="STRONG",
        )

        reg.update_telemetry("fd-1", low_batt_telem)
        reg.update_telemetry("fd-2", fresh_telem)
        # Fix roles via direct assignment since update_telemetry infers from sensors
        with reg._lock:  # pyright: ignore[reportPrivateUsage]
            reg._drones["fd-1"].role = "FIREFIGHTING"  # pyright: ignore[reportPrivateUsage]
            reg._drones["fd-2"].role = "FIREFIGHTING"  # pyright: ignore[reportPrivateUsage]

        snap = SwarmStatusSnapshot(
            leader_id="sl-1",
            fire_id="fire-1",
            timestamp=time.time(),
            fire_intensity="HIGH",
        )

        assignments = tactics.reassess(snap, reg)
        returning = [a for a in assignments if a.task == "RETURNING"]
        assert any(a.drone_id == "fd-1" for a in returning)
        # fd-2 should be assigned as fresh replacement IF it is fresh/idle
        # It has task IDLE and battery > threshold
        _suppressing = [a for a in assignments if a.task == "SUPPRESSING"]


# fd-2 might appear as fresh replacement
