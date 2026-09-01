from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.node_registry.registry import NodeRegistry
from core.state.fire_state_store import FireRecord, FireStateStore
from wfc_shared.enums.capabilities import DISPATCH_COMMANDS, SWARM_LEAD
from wfc_shared.enums.fire_status import ACTIVE  # pyright: ignore[reportUnusedImport]


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
    return FireRecord(fire_id="fire-ci-01", state=ACTIVE, zone="zone_alpha", severity="HIGH", sensor_id="s1")
