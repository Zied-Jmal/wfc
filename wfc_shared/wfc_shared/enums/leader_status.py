"""
wfc_shared.enums.leader_status
================================
Leader-specific lifecycle status, tracked inside the swarm layer.
Separate from node_status (which is the commander-side view).
"""

from __future__ import annotations

from typing import Final

LEADER_ACTIVE: Final[str] = "LEADER_ACTIVE"  # currently leading a fire response
LEADER_AVAILABLE: Final[str] = "LEADER_AVAILABLE"  # in pool, ready to be dispatched
LEADER_FAILED: Final[str] = "LEADER_FAILED"  # declared dead by heartbeat monitor
LEADER_RECOVERED: Final[str] = "LEADER_RECOVERED"  # came back after FAILED; not auto-active
