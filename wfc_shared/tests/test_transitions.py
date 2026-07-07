"""Tests for wfc_shared fire status transitions — verifies the state machine completeness."""

from __future__ import annotations

from wfc_shared.enums.fire_status import (
    FIRE_TRANSITIONS,
    TERMINAL_FIRE_STATES,
    IGNITED, ACTIVE, SPREADING, CONTAINED, SUPPRESSED, EXTINGUISHED,
)


class TestFireTransitions:
    def test_all_states_have_transitions(self) -> None:
        all_states = {IGNITED, ACTIVE, SPREADING, CONTAINED, SUPPRESSED, EXTINGUISHED}
        for state in all_states:
            assert state in FIRE_TRANSITIONS, f"State {state} missing from FIRE_TRANSITIONS"

    def test_extinguished_is_terminal(self) -> None:
        assert EXTINGUISHED in TERMINAL_FIRE_STATES
        assert FIRE_TRANSITIONS[EXTINGUISHED] == frozenset()

    def test_ignited_can_go_to_active(self) -> None:
        assert ACTIVE in FIRE_TRANSITIONS[IGNITED]

    def test_ignited_can_go_to_suppressed(self) -> None:
        assert SUPPRESSED in FIRE_TRANSITIONS[IGNITED]

    def test_active_can_go_to_spreading(self) -> None:
        assert SPREADING in FIRE_TRANSITIONS[ACTIVE]

    def test_active_can_go_to_contained(self) -> None:
        assert CONTAINED in FIRE_TRANSITIONS[ACTIVE]

    def test_active_can_go_to_suppressed(self) -> None:
        assert SUPPRESSED in FIRE_TRANSITIONS[ACTIVE]

    def test_spreading_can_go_back_to_active(self) -> None:
        assert ACTIVE in FIRE_TRANSITIONS[SPREADING]

    def test_contained_can_go_to_suppressed(self) -> None:
        assert SUPPRESSED in FIRE_TRANSITIONS[CONTAINED]

    def test_suppressed_can_go_to_extinguished(self) -> None:
        assert EXTINGUISHED in FIRE_TRANSITIONS[SUPPRESSED]

    def test_suppressed_can_reactivate(self) -> None:
        assert ACTIVE in FIRE_TRANSITIONS[SUPPRESSED]

    def test_no_invalid_transitions(self) -> None:
        all_states = {IGNITED, ACTIVE, SPREADING, CONTAINED, SUPPRESSED, EXTINGUISHED}
        for state, allowed in FIRE_TRANSITIONS.items():
            for target in allowed:
                assert target in all_states, f"Invalid target {target} from {state}"
