# TEST: U-DRONE-001, U-DRONE-002

import time

from wfc_shared.schemas.telemetry import DroneTelemetry


class TestDroneRegistry:
    def test_role_inference_from_thermal_sensor(self):
        from core.state.drone_registry import DroneRegistry

        reg = DroneRegistry()

        telem = DroneTelemetry(
            drone_id="sd-99",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.8, 10.18),
            battery_wh=200.0,
            battery_pct=0.85,
            thermal_peak_temp_c=300.0,
            task="SCOUTING",
            connectivity="STRONG",
        )
        reg.update_telemetry("sd-99", telem)

        scouts = reg.get_by_role("SCOUT")
        assert any(d.drone_id == "sd-99" for d in scouts)

    def test_role_inference_from_payload(self):
        from core.state.drone_registry import DroneRegistry

        reg = DroneRegistry()

        telem = DroneTelemetry(
            drone_id="fd-99",
            leader_id="sl-1",
            timestamp=time.time(),
            position=(36.8, 10.18),
            battery_wh=200.0,
            battery_pct=0.85,
            payload_litres=5.0,
            task="SUPPRESSING",
            connectivity="STRONG",
        )
        reg.update_telemetry("fd-99", telem)

        fighters = reg.get_by_role("FIREFIGHTING")
        assert any(d.drone_id == "fd-99" for d in fighters)

    def test_get_lost_based_on_last_seen_not_connectivity(self):
        from core.state.drone_registry import DroneRegistry

        reg = DroneRegistry(stale_threshold=5.0)

        telem = DroneTelemetry(
            drone_id="sd-1",
            leader_id="sl-1",
            timestamp=time.time() - 6,
            position=(36.8, 10.18),
            battery_wh=200.0,
            battery_pct=0.85,
            thermal_peak_temp_c=300.0,
            task="SCOUTING",
            connectivity="STRONG",
        )
        reg.update_telemetry("sd-1", telem)
        # Directly set last_seen to be old
        with reg._lock:  # pyright: ignore[reportPrivateUsage]
            reg._drones["sd-1"].last_seen = time.time() - 6  # pyright: ignore[reportPrivateUsage]

        lost = reg.get_lost()
        assert any(d.drone_id == "sd-1" for d in lost)
