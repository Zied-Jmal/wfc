"""wfc_shared.enums.node_status
==============================
Node lifecycle state constants.
State machine (STRICT)
-----------------------
UNREGISTERED REGISTERED ACTIVE OFFLINE
(heartbeat recovery from OFFLINE)
Rules
-----
R1 : Heartbeat from UNREGISTERED node is silently ignored.
R2 : Registration sets REGISTERED, never ACTIVE.
R3 : ACTIVE is system-granted only after the first heartbeat is received.
R4 : OFFLINE is set only by the system (heartbeat timeout or LWT);
a node never declares itself OFFLINE directly.
R5 : RuleEngine queries filter on ACTIVE only - REGISTERED nodes do
not receive commands.
"""

from __future__ import annotations

from typing import Final

UNREGISTERED: Final[str] = "UNREGISTERED"   # not yet known to the system
REGISTERED: Final[str] = "REGISTERED"     # announced; waiting for first heartbeat
ACTIVE: Final[str] = "ACTIVE"         # heartbeat received; eligible for commands
OFFLINE: Final[str] = "OFFLINE"        # declared dead (timeout or LWT)
