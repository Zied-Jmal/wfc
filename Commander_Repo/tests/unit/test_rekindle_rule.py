# TEST: U-RULE-014 - RekindleDetectionRule

from __future__ import annotations
from typing import Any

import pytest
from wfc_shared.enums.fire_status import SUPPRESSED, EXTINGUISHED, ACTIVE
from core.rule_engine.rules.rekindled import RekindleDetectionRule
from core.rule_engine.context import RuleContext
from core.rule_engine.trigger import EvalTrigger
from core.state.fire_state_store import FireRecord

class TestRekindleDetectionRule:
    @pytest.fixture
    def rekindled_ctx(self):
        ctx = RuleContext(trigger=EvalTrigger.REKINDLED, event_log=None)  # pyright: ignore
        ctx._rekindled_payload = {"timestamp": 1234.5}  # pyright: ignore[reportAttributeAccessIssue]
        return ctx

    def test_triggers_on_rekindled_suppressed(self, rekindled_ctx: Any) -> None:
        fire = FireRecord(fire_id="fire-rk-01", state=SUPPRESSED, zone="zone_a",
                          severity="LOW", sensor_id="s1")
        rule = RekindleDetectionRule()
        result = rule.evaluate(fire, None, rekindled_ctx)  # pyright: ignore[reportArgumentType]
        assert result.triggered == True
        assert result.state_updates["state"] == ACTIVE  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]
        assert result.state_updates["rekindled_at"] == 1234.5  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]

    def test_triggers_on_rekindled_extinguished(self, rekindled_ctx: Any) -> None:
        fire = FireRecord(fire_id="fire-rk-02", state=EXTINGUISHED, zone="zone_a",
                          severity="LOW", sensor_id="s1")
        rule = RekindleDetectionRule()
        result = rule.evaluate(fire, None, rekindled_ctx)  # pyright: ignore[reportArgumentType]
        assert result.triggered == True

    def test_no_trigger_when_already_active(self, rekindled_ctx: Any) -> None:
        fire = FireRecord(fire_id="fire-rk-03", state=ACTIVE, zone="zone_a",
                          severity="LOW", sensor_id="s1")
        rule = RekindleDetectionRule()
        result = rule.evaluate(fire, None, rekindled_ctx)  # pyright: ignore[reportArgumentType]
        assert result.triggered == False
        assert result.reason == "not_suppressed"

    def test_no_trigger_on_wrong_trigger(self) -> None:
        ctx = RuleContext(trigger=EvalTrigger.NEW_FIRE, event_log=None)  # pyright: ignore
        fire = FireRecord(fire_id="fire-rk-04", state=SUPPRESSED, zone="zone_a",
                          severity="LOW", sensor_id="s1")
        rule = RekindleDetectionRule()
        result = rule.evaluate(fire, None, ctx)  # pyright: ignore[reportArgumentType]
        assert result.triggered == False
        assert result.reason == "not_rekindled_trigger"
