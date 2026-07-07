# TEST: U-RULE-006 - HighSeverityRule

from __future__ import annotations
from typing import Any

import pytest  # pyright: ignore[reportUnusedImport]
from wfc_shared.enums.capabilities import SWARM_LEAD, DISPATCH_COMMANDS  # pyright: ignore[reportUnusedImport]
from core.rule_engine.rules.high_severity import HighSeverityRule
from core.rule_engine.rule import RuleResult  # pyright: ignore[reportUnusedImport]

class TestHighSeverityRule:
    def test_triggers_on_high_severity(self, empty_registry: Any) -> None:
        reg = empty_registry
        reg.register("cmd-1", "COMMANDER", [DISPATCH_COMMANDS])
        reg.heartbeat("cmd-1")
        reg.register("cmd-2", "BACKUP", [DISPATCH_COMMANDS])
        reg.heartbeat("cmd-2")

        from wfc_shared.enums.fire_status import ACTIVE, IGNITED  # pyright: ignore[reportUnusedImport]
        from core.state.fire_state_store import FireRecord
        fire = FireRecord(
            fire_id="fire-high-01", state=ACTIVE, zone="zone_a",
            severity="HIGH", sensor_id="s1", location_coords=(36.8, 10.18)
        )
        rule = HighSeverityRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered == True
        assert len(result.commands) == 2
        for cmd in result.commands:
            assert cmd.command_type == "ESCALATE_FIRE"
        assert rule.requires_approval == True

    def test_no_trigger_for_low_severity(self, empty_registry: Any) -> None:
        reg = empty_registry
        reg.register("cmd-1", "COMMANDER", [DISPATCH_COMMANDS])
        reg.heartbeat("cmd-1")
        from wfc_shared.enums.fire_status import ACTIVE
        from core.state.fire_state_store import FireRecord
        fire = FireRecord(
            fire_id="fire-low-01", state=ACTIVE, zone="zone_a",
            severity="LOW", sensor_id="s1"
        )
        rule = HighSeverityRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered == False

    def test_no_trigger_for_contained_state(self, empty_registry: Any) -> None:
        reg = empty_registry
        reg.register("cmd-1", "COMMANDER", [DISPATCH_COMMANDS])
        reg.heartbeat("cmd-1")
        from wfc_shared.enums.fire_status import CONTAINED
        from core.state.fire_state_store import FireRecord
        fire = FireRecord(
            fire_id="fire-cont-01", state=CONTAINED, zone="zone_a",
            severity="HIGH", sensor_id="s1"
        )
        rule = HighSeverityRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered == False

    def test_no_escalation_target(self, empty_registry: Any) -> None:
        from wfc_shared.enums.fire_status import ACTIVE
        from core.state.fire_state_store import FireRecord
        # Register node without DISPATCH_COMMANDS
        fire = FireRecord(
            fire_id="fire-no-esc", state=ACTIVE, zone="zone_a",
            severity="HIGH", sensor_id="s1"
        )
        rule = HighSeverityRule()
        result = rule.evaluate(fire, empty_registry)
        assert result.triggered == False
        assert result.reason == "no_backup_commander_available"
