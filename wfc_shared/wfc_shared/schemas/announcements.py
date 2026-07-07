"""
wfc_shared.schemas.announcements - Retained MQTT payload published by every node.
Topic : wfc/registry/announce/{node_id}  |  QoS 1  |  Retained = YES
Broker stores the last message so late-joining nodes discover all existing
nodes on subscribe.
LWT (Last Will and Testament)
-----------------------------
Every node configures its broker LWT to publish this schema with
status="OFFLINE" to its own announce topic before connecting.
This ensures the commander sees OFFLINE even on crash.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

# Constrained type aliases
NodeType = Literal[
    "CENTRAL_COMMANDER",
    "BACKUP_COMMANDER",
    "SWARM_LEADER",
    "SCOUT_DRONE",
    "FIREFIGHTING_DRONE",
    "OPERATOR_HMI",
    "INFRA_NODE",
    "VIRTUAL",
    "DRONE_NODE",
]
"""Valid node type identifiers from wfc_shared.enums.node_types."""
NodeStatus = Literal["ONLINE", "OFFLINE"]
"""Node liveness status - ONLINE on startup, OFFLINE on stop or LWT."""
Capability = Literal[
    "DISPATCH_COMMANDS",
    "HUMAN_APPROVAL",
    "FIRE_DETECTION",
    "SWARM_LEAD",
    "LEADER_BACKUP",
    "SCOUT",
    "FIREFIGHTING",
    "AERIAL_RESPONSE",
    "RECEIVE_COMMANDS",
    "HEARTBEAT",
    "TELEMETRY",
]
"""Valid node capability strings from wfc_shared.enums.capabilities."""


class NodeAnnouncement(BaseModel):
    """Retained announcement published by every node on startup, graceful stop,
    and injected by the broker as LWT on crash.

    Args:
        node_id: Globally unique identifier (e.g. "sl-A-01", "fd-A-03").
        node_type: One of the NodeType constants.
        capabilities: List of Capability constants this node supports.
        status: ONLINE on startup; OFFLINE on stop or LWT trigger.
        host: Optional hostname/IP for diagnostic purposes.
        announced_at: UNIX epoch seconds (UTC).
        zone: Zone label matching FirePayload.zone (e.g. "zone_alpha").
        location: (lat_deg, lon_deg) WGS-84 for proximity dispatch.
        election: Swarm leaders only - populated on election-win.
    """

    node_id: str
    """Globally unique identifier (e.g. "sl-A-01", "fd-A-03")."""
    node_type: NodeType
    """One of the NodeType constants."""
    capabilities: list[Capability] = Field(default_factory=list)  # type: ignore[reportUnknownMemberType]
    """List of Capability constants this node supports."""
    status: NodeStatus = "ONLINE"
    """ONLINE on startup; OFFLINE on stop or LWT trigger."""
    host: str | None = None
    """Optional hostname/IP for diagnostic purposes."""
    announced_at: float = Field(default_factory=time.time)
    """UNIX epoch seconds (UTC)."""
    zone: str | None = None
    """Zone label matching FirePayload.zone (e.g. "zone_alpha")."""
    location: tuple[float, float] | None = None
    """(lat_deg, lon_deg) WGS-84 for proximity dispatch."""
    election: dict[str, Any] | None = None
    """Swarm leaders only - populated on election-win announce."""
