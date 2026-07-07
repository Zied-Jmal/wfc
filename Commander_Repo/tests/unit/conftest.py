from __future__ import annotations

import pytest
from wfc_shared.enums.fire_status import ACTIVE, IGNITED, CONTAINED, SUPPRESSED, EXTINGUISHED, SPREADING  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.node_status import REGISTERED, ACTIVE as NODE_ACTIVE, OFFLINE  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.capabilities import SWARM_LEAD, DISPATCH_COMMANDS  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.command_types import RESPOND_TO_FIRE, CONTAIN_FIRE, STAND_DOWN, ESCALATE_FIRE, CONFIRM_LEADERSHIP  # pyright: ignore[reportUnusedImport]
from core.state.fire_state_store import FireRecord, FireStateStore
from core.rule_engine.rule import RuleResult  # pyright: ignore[reportUnusedImport]
from core.rule_engine.context import RuleContext
from core.rule_engine.trigger import EvalTrigger
from core.node_registry.registry import NodeRegistry
import time  # pyright: ignore[reportUnusedImport]

@pytest.fixture
def fire_record():
    """   Create a minimal ACTIVE fire record for testing."""

    return FireRecord(
        fire_id="test-fire-001",
        state=ACTIVE,
        zone="zone_alpha",
        severity="MEDIUM",
        sensor_id="sensor-01",
        location_coords=(36.80, 10.18),
    )

@pytest.fixture
def fire_record_no_coords():
    return FireRecord(
        fire_id="test-fire-002",
        state=ACTIVE,
        zone="zone_alpha",
        severity="MEDIUM",
        sensor_id="sensor-01",
        location_coords=None,
    )

@pytest.fixture
def fire_record_assigned():
    return FireRecord(
        fire_id="test-fire-003",
        state=ACTIVE,
        zone="zone_alpha",
        severity="MEDIUM",
        sensor_id="sensor-01",
        assigned_nodes=["sl-1"],
    )

@pytest.fixture
def empty_registry():
    return NodeRegistry()

@pytest.fixture
def populated_registry():
    reg = NodeRegistry()
    reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD], zone="zone_alpha", location=(36.81, 10.19))
    reg.register("sl-2", "SWARM_LEADER", [SWARM_LEAD], zone="zone_bravo", location=(37.50, 11.00))
    reg.register("sl-3", "SWARM_LEADER", [SWARM_LEAD], zone="zone_alpha", location=(36.805, 10.185))
    # Grant ACTIVE to all via heartbeat
    for nid in ["sl-1", "sl-2", "sl-3"]:
        reg.heartbeat(nid)
    return reg

@pytest.fixture
def context():
    return RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None)  # pyright: ignore

@pytest.fixture
def intensity_context():
    return RuleContext(trigger=EvalTrigger.INTENSITY_UPDATE, event_log=None)  # pyright: ignore

@pytest.fixture
def rekindled_context():
    ctx = RuleContext(trigger=EvalTrigger.REKINDLED, event_log=None)  # pyright: ignore
    ctx._rekindled_payload = {"timestamp": 1234.5}  # pyright: ignore[reportAttributeAccessIssue]
    return ctx

@pytest.fixture
def fire_state_store():
    return FireStateStore()
