import pytest
import time
from wfc_shared.schemas.telemetry import DroneTelemetry

@pytest.fixture
def scout_telem():
    return DroneTelemetry(
        drone_id="sd-1", leader_id="sl-1", timestamp=time.time(),
        position=(36.80, 10.18), altitude_m_amsl=100.0,
        battery_wh=200.0, battery_pct=0.85,
        payload_litres=None, payload_kg=None,  # pyright: ignore[reportArgumentType]
        task="SCOUTING", connectivity="STRONG",
        thermal_peak_temp_c=300.0, thermal_coverage_pct=0.75,
        smoke_density_mg_m3=50.0, smoke_optical_density=0.3,
        flame_height_m=5.0, distance_to_flame_m=100.0,
        perimeter_estimate_m=150.0,
        wind_speed_mps=5.0, wind_direction_deg=225.0,
        litres_delivered=None, suppression_effectiveness_pct=None,
        drop_passes=None, pump_active=None,
    )

@pytest.fixture
def fighter_telem():
    return DroneTelemetry(
        drone_id="fd-1", leader_id="sl-1", timestamp=time.time(),
        position=(36.81, 10.19), altitude_m_amsl=80.0,
        battery_wh=180.0, battery_pct=0.75,
        payload_litres=8.0, payload_kg=8.0,
        task="SUPPRESSING", connectivity="STRONG",
        thermal_peak_temp_c=None, thermal_coverage_pct=None,
        smoke_density_mg_m3=None, smoke_optical_density=None,
        flame_height_m=None, distance_to_flame_m=80.0,
        perimeter_estimate_m=None,
        wind_speed_mps=None, wind_direction_deg=None,
        litres_delivered=5.0, suppression_effectiveness_pct=0.5,
        drop_passes=2, pump_active=True,
    )
