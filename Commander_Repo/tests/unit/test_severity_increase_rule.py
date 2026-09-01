# TEST: U-RULE-015 - SeverityIncreaseRule

from __future__ import annotations

from typing import Any

import pytest

from core.rule_engine.context import RuleContext
from core.rule_engine.rules.severity_increase import SeverityIncreaseRule
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord
from wfc_shared.enums.fire_status import ACTIVE


class TestSeverityIncreaseRule:
    @pytest.fixture
    def intensity_ctx(self):
        ctx = RuleContext(trigger=EvalTrigger.INTENSITY_UPDATE, event_log=None)  # pyright: ignore
        return ctx

    def test_ignores_unchanged_severity(self, intensity_ctx: Any) -> None:
        intensity_ctx._intensity_payload = {"new_intensity": "HIGH"}
        fire = FireRecord(fire_id="fire-si-01", state=ACTIVE, zone="zone_a", severity="HIGH", sensor_id="s1")
        rule = SeverityIncreaseRule()
        result = rule.evaluate(fire, None, intensity_ctx)  # pyright: ignore[reportArgumentType]
        assert not result.triggered
        assert result.reason == "severity_unchanged"

    def test_triggers_on_increase(self, intensity_ctx: Any) -> None:
        intensity_ctx._intensity_payload = {"new_intensity": "CRITICAL"}
        fire = FireRecord(fire_id="fire-si-02", state=ACTIVE, zone="zone_a", severity="HIGH", sensor_id="s1")
        rule = SeverityIncreaseRule()
        result = rule.evaluate(fire, None, intensity_ctx)  # pyright: ignore[reportArgumentType]
        assert result.triggered
        assert result.state_updates["severity"] == "CRITICAL"  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]

    def test_no_trigger_on_wrong_event(self) -> None:
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None)  # pyright: ignore
        fire = FireRecord(fire_id="fire-si-03", state=ACTIVE, zone="zone_a", severity="MEDIUM", sensor_id="s1")
        rule = SeverityIncreaseRule()
        result = rule.evaluate(fire, None, ctx)  # pyright: ignore[reportArgumentType]
        assert not result.triggered
        assert result.reason == "not_intensity_trigger"

    def test_no_trigger_no_intensity_data(self, intensity_ctx: Any) -> None:
        # No _intensity_payload set
        fire = FireRecord(fire_id="fire-si-04", state=ACTIVE, zone="zone_a", severity="MEDIUM", sensor_id="s1")
        rule = SeverityIncreaseRule()
        result = rule.evaluate(fire, None, intensity_ctx)  # pyright: ignore[reportArgumentType]
        assert not result.triggered
        assert result.reason == "no_intensity_data"
