from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from wfc_shared.enums.capabilities import SWARM_LEAD, DISPATCH_COMMANDS
from wfc_shared.enums.fire_status import ACTIVE, IGNITED  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.command_types import RESPOND_TO_FIRE, ESCALATE_FIRE  # pyright: ignore[reportUnusedImport]
from core.node_registry.registry import NodeRegistry
from core.state.fire_state_store import FireStateStore, FireRecord
from core.rule_engine.engine import RuleEngine  # pyright: ignore[reportUnusedImport]
from core.rule_engine.context import RuleContext  # pyright: ignore[reportUnusedImport]
from core.rule_engine.trigger import EvalTrigger  # pyright: ignore[reportUnusedImport]

@pytest.fixture
def fake_dispatcher():
    d = MagicMock()
    d.send.return_value = "trace-mock"
    return d

@pytest.fixture
def fake_mqtt():
    return MagicMock()

@pytest.fixture
def registry_with_leaders():
    reg = NodeRegistry()
    reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD], zone="zone_alpha", location=(36.81, 10.19))
    reg.register("cmd-1", "CENTRAL_COMMANDER", [DISPATCH_COMMANDS])
    for nid in ["sl-1", "cmd-1"]:
        reg.heartbeat(nid)
    return reg

@pytest.fixture
def fire_state():
    return FireStateStore()

@pytest.fixture
def high_fire_record():
    return FireRecord(
        fire_id="fire-ci-01", state=ACTIVE, zone="zone_alpha",
        severity="HIGH", sensor_id="s1"
    )
