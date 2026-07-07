# TEST: U-FIRE-001 to U-FIRE-004

from __future__ import annotations

import pytest  # pyright: ignore[reportUnusedImport]
import time
from wfc_shared.enums.fire_status import IGNITED, ACTIVE, SUPPRESSED, EXTINGUISHED, FIRE_TRANSITIONS, TERMINAL_FIRE_STATES  # pyright: ignore[reportUnusedImport]
from core.state.fire_state_store import FireStateStore, FireRecord  # pyright: ignore[reportUnusedImport]

class TestFireStateStore:
    def test_transition_rejects_invalid(self) -> None:
        store = FireStateStore()
        store.ignite("fire-1", "zone_a", "MEDIUM", "s1")
        # Direct IGNITED -> EXTINGUISHED should be invalid (skips lifecycle)
        result = store.transition("fire-1", EXTINGUISHED)
        assert result is None
        rec = store.get("fire-1")
        assert rec.state == IGNITED  # pyright: ignore[reportOptionalMemberAccess]

    def test_transition_on_terminal_always_rejected(self) -> None:
        store = FireStateStore()
        store.ignite("fire-2", "zone_a", "LOW", "s1")
        # Force terminal state
        fire = store.get("fire-2")  # pyright: ignore[reportUnusedVariable]
        terminal_state = list(TERMINAL_FIRE_STATES)[0]  # pyright: ignore[reportUnusedVariable]
        # Manually create a terminal record
        from wfc_shared.enums.fire_status import EXTINGUISHED
        store.transition("fire-2", ACTIVE)
        # Try to transition from EXTINGUISHED back to anything
        from wfc_shared.enums.fire_status import CONTAINED  # pyright: ignore[reportUnusedImport]
        # Put it in terminal state
        store._fires["fire-2"] = store._fires["fire-2"].model_copy(update={"state": EXTINGUISHED})  # pyright: ignore[reportOptionalMemberAccess, reportPrivateUsage]
        result = store.transition("fire-2", ACTIVE)
        assert result is not None
        assert result.state == EXTINGUISHED

    def test_apply_snapshot_lww(self) -> None:
        store = FireStateStore()
        store.ignite("fire-3", "zone_a", "MEDIUM", "s1")
        data = {  # pyright: ignore[reportUnknownVariableType]
            "state": ACTIVE, "zone": "zone_a", "severity": "HIGH",
            "sensor_id": "s1", "location_coords": None,
            "assigned_nodes": [], "leader_term": 0,
            "updated_at": 50.0  # older than existing
        }
        result = store.apply_snapshot_record("fire-3", data)  # pyright: ignore[reportUnknownArgumentType]
        # Should be False because existing updated_at > 50.0
        assert result == False

    def test_apply_snapshot_merges_assigned_nodes(self) -> None:
        store = FireStateStore()
        store.ignite("fire-4", "zone_a", "MEDIUM", "s1")
        fire = store.get("fire-4")
        fire = fire.model_copy(update={"assigned_nodes": ["sl-1", "sl-2"], "updated_at": time.time() + 100})  # pyright: ignore[reportOptionalMemberAccess]
        store._fires["fire-4"] = fire  # pyright: ignore[reportPrivateUsage]
        data = {
            "state": IGNITED, "zone": "zone_a", "severity": "MEDIUM",
            "sensor_id": "s1", "location_coords": None,
            "assigned_nodes": ["sl-2", "sl-3"],
            "leader_term": 0,
            "updated_at": time.time() + 200
        }
        store.apply_snapshot_record("fire-4", data)
        merged = store.get("fire-4")
        assert merged.assigned_nodes == ["sl-1", "sl-2", "sl-3"]  # pyright: ignore[reportOptionalMemberAccess, reportOptionalSubscript]
