# TEST: U-TELE-001 to U-TELE-004

import time
from wfc_shared.schemas.telemetry import DroneTelemetry

class TestTelemetryAggregator:
    def test_calc_fire_intensity_thresholds(self):
        from core.aggregator.telemetry_aggregator import TelemetryAggregator
        agg = TelemetryAggregator("sl-1")

        def make_scout(temp_c: float) -> DroneTelemetry:
            return DroneTelemetry(
                drone_id="sd-1", leader_id="sl-1", timestamp=time.time(),
                position=(36.8, 10.18),
                battery_wh=200.0, battery_pct=0.85,
                task="SCOUTING", connectivity="STRONG",
                thermal_peak_temp_c=temp_c,
            )

        assert agg._calc_fire_intensity([make_scout(399.9)]) == "HIGH"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(400.0)]) == "CRITICAL"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(279.9)]) == "MEDIUM"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(280.0)]) == "HIGH"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(179.9)]) == "LOW"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(180.0)]) == "MEDIUM"  # pyright: ignore[reportPrivateUsage]
        assert agg._calc_fire_intensity([make_scout(0.0)]) == "LOW"  # pyright: ignore[reportPrivateUsage]

    def test_empty_snapshot_returns_idle(self):
        from core.aggregator.telemetry_aggregator import TelemetryAggregator
        agg = TelemetryAggregator("sl-1")
        snap = agg.snapshot()
        assert snap.status == "IDLE"
        assert snap.active_drones == 0

    def test_calc_suppression_returns_none_for_tiny_perimeter(self):
        from core.aggregator.telemetry_aggregator import TelemetryAggregator
        agg = TelemetryAggregator("sl-1")
        result = agg._calc_suppression(100.0, 0.5, "HIGH")  # pyright: ignore[reportPrivateUsage]
        assert result is None
        result = agg._calc_suppression(100.0, 0.0, "HIGH")  # pyright: ignore[reportPrivateUsage]
        assert result is None
        result = agg._calc_suppression(100.0, None, "HIGH")  # pyright: ignore[reportPrivateUsage]
        assert result is None

    def test_ingest_window_eviction(self):
        from core.aggregator.telemetry_aggregator import TelemetryAggregator
        agg = TelemetryAggregator("sl-1")

        def make_telem(timestamp: float) -> DroneTelemetry:
            return DroneTelemetry(
                drone_id="sd-1", leader_id="sl-1", timestamp=timestamp,
                position=(36.8, 10.18),
                battery_wh=200.0, battery_pct=0.85,
                thermal_peak_temp_c=300.0,
                task="SCOUTING", connectivity="STRONG",
            )

        t0 = time.time() - 10
        t30 = time.time() - 5
        t70 = time.time()

        agg.ingest(make_telem(t0))
        agg.ingest(make_telem(t30))
        agg.ingest(make_telem(t70))

        with agg._lock:  # pyright: ignore[reportPrivateUsage]
            dq = agg._history.get("sd-1")  # pyright: ignore[reportPrivateUsage]
        assert dq is not None
        ts_list = [item[0] for item in dq]
        # The t0 timestamp is now > 60s old relative to time.time() so it should be evicted
        for ts in ts_list:
            assert (time.time() - ts) <= 60.0
