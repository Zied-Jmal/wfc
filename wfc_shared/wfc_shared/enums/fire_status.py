"""wfc_shared.enums.fire_status
==============================
Fire lifecycle state constants and enforced transition table.
FireStateStore is the ONLY writer of fire state.
State machine
--------------
IGNITED ACTIVE SPREADING CONTAINED
SUPPRESSED EXTINGUISHED (terminal)
States
------
IGNITED : fire first detected by a ground sensor
ACTIVE : confirmed; swarm leader dispatched
SPREADING : fire intensity increased beyond initial report
(triggered by SeverityIncreaseRule in the rule engine)
CONTAINED : perimeter held; fire no longer growing
SUPPRESSED : extinguishing in progress; stand-down issued
EXTINGUISHED : confirmed out; mission closed (terminal)
Rules
-----
F1 : Only FireStateStore calls transition().
F2 : Sensor/leader events trigger evaluation; state owns truth.
F3 : State always exists as a snapshot (SQLite persistence).
F4 : EXTINGUISHED is the only terminal state; no further transitions allowed.
"""

from __future__ import annotations

from typing import Final

# State constants
IGNITED: Final[str] = "IGNITED"
ACTIVE: Final[str] = "ACTIVE"
SPREADING: Final[str] = "SPREADING"
CONTAINED: Final[str] = "CONTAINED"
SUPPRESSED: Final[str] = "SUPPRESSED"
EXTINGUISHED: Final[str] = "EXTINGUISHED"

# Transition table
# FireStateStore.transition() rejects any move NOT listed here.
FIRE_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    IGNITED:      frozenset({ACTIVE, SUPPRESSED}),
    ACTIVE:       frozenset({SPREADING, CONTAINED, SUPPRESSED}),
    SPREADING:    frozenset({ACTIVE, CONTAINED, SUPPRESSED}),
    CONTAINED:    frozenset({SUPPRESSED, ACTIVE, SPREADING}),
    SUPPRESSED:   frozenset({EXTINGUISHED, ACTIVE}),
    EXTINGUISHED: frozenset(),   # terminal
}

TERMINAL_FIRE_STATES: Final[frozenset[str]] = frozenset({EXTINGUISHED})
