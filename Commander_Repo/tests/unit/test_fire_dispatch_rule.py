# TEST: U-RULE-001 - FireDispatchRule: proximity selection
# TEST: U-RULE-002 - FireDispatchRule: zone fallback
# TEST: U-RULE-003 - FireDispatchRule: any-available last resort
# TEST: U-RULE-004 - FireDispatchRule: no double-dispatch
# TEST: U-RULE-005 - FireDispatchRule: no candidates at all

from __future__ import annotations

from typing import Any

from core.rule_engine.rules.fire_dispatch import FireDispatchRule
from wfc_shared.enums.capabilities import SWARM_LEAD  # pyright: ignore[reportUnusedImport]


class TestFireDispatchRule:
    def test_proximity_selection(self, fire_record: Any, populated_registry: Any) -> None:
        rule = FireDispatchRule()
        result = rule.evaluate(fire_record, populated_registry)
        assert result.triggered
        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd.command_type == "RESPOND_TO_FIRE"
        assert cmd.target_node == "sl-3"  # closest
        assert "proximity" in result.reason

    def test_zone_fallback(self, fire_record_no_coords: Any, populated_registry: Any) -> None:
        rule = FireDispatchRule()
        result = rule.evaluate(fire_record_no_coords, populated_registry)
        assert result.triggered
        assert result.commands[0].target_node in ("sl-1", "sl-3")
        assert "zone_match" in result.reason

    def test_any_available(self, fire_record_no_coords: Any, empty_registry: Any) -> None:
        # Register one node with NO zone match, no coords
        reg = empty_registry
        reg.register("sl-9", "SWARM_LEADER", [SWARM_LEAD], zone="zone_bravo")
        reg.heartbeat("sl-9")
        fire = fire_record_no_coords
        fire = fire.model_copy(update={"zone": "zone_alpha"})
        rule = FireDispatchRule()
        result = rule.evaluate(fire, reg)
        assert result.triggered
        assert result.commands[0].target_node == "sl-9"
        assert "any_available" in result.reason

    def test_no_double_dispatch(self, fire_record_assigned: Any, populated_registry: Any) -> None:
        rule = FireDispatchRule()
        result = rule.evaluate(fire_record_assigned, populated_registry)
        assert not result.triggered
        assert result.reason == "fire_already_assigned"

    def test_no_candidates(self, fire_record: Any, empty_registry: Any) -> None:
        rule = FireDispatchRule()
        # No exception should be raised
        result = rule.evaluate(fire_record, empty_registry)
        assert not result.triggered
        assert result.reason == "no_available_swarm_leaders"

    def test_wrong_state(self, fire_record: Any, populated_registry: Any) -> None:
        # Modify fire state to IGNITED - should not trigger
        fire = fire_record.model_copy(update={"state": "IGNITED"})
        rule = FireDispatchRule()
        result = rule.evaluate(fire, populated_registry)
        assert not result.triggered
