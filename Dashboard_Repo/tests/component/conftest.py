from __future__ import annotations

import pytest

from dashboard.mqtt_bridge import MQTTBridge
from dashboard.state import SwarmState


@pytest.fixture
def swarm_state() -> SwarmState:
    from dashboard.state import SwarmState

    return SwarmState()


@pytest.fixture
def bridge(swarm_state: SwarmState) -> MQTTBridge:
    from dashboard.mqtt_bridge import MQTTBridge

    b = MQTTBridge()
    # Override internal state reference
    from dashboard import state as state_module

    state_module.swarm_state = swarm_state
    return b
