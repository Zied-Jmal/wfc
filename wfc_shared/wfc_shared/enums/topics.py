"""
wfc_shared.enums.topics
=======================
Canonical MQTT topic constants and builder helpers.
NEVER use raw string literals anywhere in the codebase -
always import from here.
Topic namespace: wfc/
"""

from __future__ import annotations
from typing import Final
# Wildcard subscriptions
WFC_ALL: Final[str] = "wfc/#"
REGISTRY_ANNOUNCE_WILDCARD: Final[str] = "wfc/registry/announce/#"
TELEMETRY_WILDCARD: Final[str] = "wfc/telemetry/#"
NODES_HEARTBEAT_WILDCARD: Final[str] = "wfc/nodes/+/heartbeat"
SWARM_STATUS_SUB: Final[str] = "wfc/swarm/status/#"
SWARM_ELECTION_SUB: Final[str] = "wfc/swarm/election/#"
# Topic templates (use builder helpers - never format manually)
REGISTRY_ANNOUNCE_TARGET: Final[str] = "wfc/registry/announce/{node_id}" # retained, per-node
NODE_HEARTBEAT: Final[str] = "wfc/nodes/{node_id}/heartbeat"
COMMAND_TARGET: Final[str] = "wfc/command/{node_id}"
TELEMETRY_TARGET: Final[str] = "wfc/telemetry/{drone_id}"
SWARM_STATUS_TARGET: Final[str] = "wfc/swarm/status/{leader_id}"
SWARM_ELECTION_TARGET: Final[str] = "wfc/swarm/election/{zone}"
SWARM_INTERNAL_TARGET: Final[str] = "wfc/swarm/internal/{node_id}"
# Prefix constants (for startswith() routing - no wildcards)
SWARM_STATUS_PREFIX: Final[str] = "wfc/swarm/status/"
SWARM_ELECTION_PREFIX: Final[str] = "wfc/swarm/election/"
# topics
ACK: Final[str] = "wfc/ack"
EVENTS_FIRE: Final[str] = "wfc/events/fire" # sensor initial detection
FIRE_INTENSITY: Final[str] = "wfc/events/fire/intensity" # leader intensity update
FIRE_VERIFIED_TOPIC: Final[str] = "wfc/events/fire/verified" # scout confirmed detection
FIRE_REKINDLED_TOPIC: Final[str] = "wfc/events/fire/rekindled" # previously suppressed, re-ignited
APPROVAL_PENDING: Final[str] = "wfc/approval/pending" # commander operator
APPROVAL_RESPONSE: Final[str] = "wfc/approval/response" # operator commander
STATE_SNAPSHOT: Final[str] = "wfc/state/snapshot" # commander every 10 s (retained)
SYSTEM_FAILOVER: Final[str] = "wfc/system/failover" # backup promotion event
SYSTEM_LEASE: Final[str] = "wfc/system/lease" # retained {owner, term, since}
# Builder helpers
def registry_announce_topic(node_id: str) -> str:
    """Per-node retained announce slot.

    Each node publishes ONLY to its own slot so MQTT retained
    storage never overwrites another node's announcement.

    Args:
        node_id: Unique identifier of the announcing node.

    Returns:
        The full MQTT topic string.
    """
    return REGISTRY_ANNOUNCE_TARGET.format(node_id=node_id)


def heartbeat_topic(node_id: str) -> str:
    """Heartbeat publish topic for node_id.

    Args:
        node_id: Unique identifier of the heartbeat publisher.

    Returns:
        The full MQTT topic string.
    """
    return NODE_HEARTBEAT.format(node_id=node_id)


def command_topic(node_id: str) -> str:
    """Commander/Leader target node command delivery topic.

    Args:
        node_id: Unique identifier of the target node.

    Returns:
        The full MQTT topic string.
    """
    return COMMAND_TARGET.format(node_id=node_id)


def telemetry_topic(drone_id: str) -> str:
    """Drone publishes raw telemetry here (drone leader only).

    Args:
        drone_id: Unique identifier of the drone.

    Returns:
        The full MQTT topic string.
    """
    return TELEMETRY_TARGET.format(drone_id=drone_id)


def swarm_status_topic(leader_id: str) -> str:
    """Leader publishes SwarmStatusSnapshot here (leader commander).

    Args:
        leader_id: Unique identifier of the swarm leader.

    Returns:
        The full MQTT topic string.
    """
    return SWARM_STATUS_TARGET.format(leader_id=leader_id)


def swarm_election_topic(zone: str) -> str:
    """Broadcast channel for election messages within a zone.

    Args:
        zone: Zone identifier for the election.

    Returns:
        The full MQTT topic string.
    """
    return SWARM_ELECTION_TARGET.format(zone=zone)


def swarm_internal_topic(node_id: str) -> str:
    """Point-to-point bully-election channel for a specific node.

    Args:
        node_id: Unique identifier of the target node.

    Returns:
        The full MQTT topic string.
    """
    return SWARM_INTERNAL_TARGET.format(node_id=node_id)
