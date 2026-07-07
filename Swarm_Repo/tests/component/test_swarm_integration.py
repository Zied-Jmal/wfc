# CI-SWARM-001: TelemetryAggregator + FireTactics.reassess() low battery flow

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'wfc_shared')))

import time
from wfc_shared.schemas.telemetry import DroneTelemetry
from core.aggregator.telemetry_aggregator import TelemetryAggregator
from core.state.drone_registry import DroneRegistry
from core.tactics.fire_tactics import FireTactics

class TestSwarmIntegration:
    def test_low_battery_flows_into_returning(self):
        agg = TelemetryAggregator("sl-1")
        reg = DroneRegistry()
        tactics = FireTactics()

# Ingest low-battery telemetry
        low_telem = DroneTelemetry(
            drone_id="fd-1", leader_id="sl-1", timestamp=time.time(),
            position=(36.8, 10.18), altitude_m_amsl=80.0,
            battery_wh=50.0, battery_pct=0.15,
            payload_litres=5.0, payload_kg=5.0,
            task="SUPPRESSING", connectivity="STRONG",
        )
        agg.ingest(low_telem)
        reg.update_telemetry("fd-1", low_telem)
        with reg._lock:  # pyright: ignore[reportPrivateUsage]
            reg._drones["fd-1"].role = "FIREFIGHTING"  # pyright: ignore[reportPrivateUsage]

        snap = agg.snapshot()
        assignments = tactics.reassess(snap, reg)

        returning = [a for a in assignments if a.task == "RETURNING"]
        assert any(a.drone_id == "fd-1" for a in returning)
