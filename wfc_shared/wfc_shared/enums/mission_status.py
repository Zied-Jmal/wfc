"""wfc_shared.enums.mission_status
================================
Mission lifecycle state constants and enforced transition table.
MissionStore is the ONLY writer of mission state.
State machine
--------------
CREATED ASSIGNED RUNNING REINFORCING COMPLETED
PAUSED ASSIGNED (re-dispatch)
FAILED (terminal)
Rules
-----
M1 : Only CentralNode/CommandDispatcher creates missions.
M2 : MissionStore is the single owner.
M3 : Mission state is authoritative; drone ACK only informs
transitions, never overrides state.
"""

from __future__ import annotations

from typing import Final

# State constants
CREATED: Final[str] = "CREATED"  # record exists; no node assigned yet
ASSIGNED: Final[str] = "ASSIGNED"  # target selected; RESPOND_TO_FIRE sent
RUNNING: Final[str] = "RUNNING"  # target ACKed RECEIVED; mission in progress
REINFORCING: Final[str] = "REINFORCING"  # additional leader dispatched to same fire
PAUSED: Final[str] = "PAUSED"  # awaiting reassignment (assigned node failed)
COMPLETED: Final[str] = "COMPLETED"  # STAND_DOWN executed; fire confirmed out
FAILED: Final[str] = "FAILED"  # no nodes available or terminal failure

# Transition table
MISSION_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    CREATED: frozenset({ASSIGNED, FAILED}),
    ASSIGNED: frozenset({RUNNING, PAUSED, FAILED}),
    RUNNING: frozenset({PAUSED, REINFORCING, COMPLETED, FAILED}),
    REINFORCING: frozenset({RUNNING, PAUSED, COMPLETED, FAILED}),
    PAUSED: frozenset({ASSIGNED, FAILED}),
    COMPLETED: frozenset(),  # terminal
    FAILED: frozenset(),  # terminal
}

TERMINAL_MISSION_STATES: Final[frozenset[str]] = frozenset({COMPLETED, FAILED})
