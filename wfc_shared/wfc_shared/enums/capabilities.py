"""
wfc_shared.enums.capabilities
==============================
Node capability string constants.
A node's capabilities list in NodeAnnouncement declares what it can do.
The RuleEngine queries capabilities to select eligible dispatch targets.
Capability sets by node type
-----------------------------
CENTRAL_COMMANDER  : [DISPATCH_COMMANDS, HUMAN_APPROVAL, FIRE_DETECTION, HEARTBEAT]
BACKUP_COMMANDER   : [DISPATCH_COMMANDS, HUMAN_APPROVAL, FIRE_DETECTION, HEARTBEAT]
SWARM_LEADER       : [SWARM_LEAD, RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY]

  (backup leader)  : [SWARM_LEAD, LEADER_BACKUP, RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY]
SCOUT_DRONE        : [SCOUT, RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY]
FIREFIGHTING_DRONE : [FIREFIGHTING, RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY]
OPERATOR_HMI       : [HUMAN_APPROVAL]

"""

from __future__ import annotations

from typing import Final

# Commander capabilities
DISPATCH_COMMANDS: Final[str] = "DISPATCH_COMMANDS"  # Can issue commands to field nodes
HUMAN_APPROVAL: Final[str] = "HUMAN_APPROVAL"  # Can act as approval authority
FIRE_DETECTION: Final[str] = "FIRE_DETECTION"  # Processes fire sensor events

# Leader capabilities
SWARM_LEAD: Final[str] = "SWARM_LEAD"  # Leads a drone group; relay target
LEADER_BACKUP: Final[str] = "LEADER_BACKUP"  # Can be promoted to lead on leader death

# Drone capabilities (mutually exclusive - a drone is exactly one type)
SCOUT: Final[str] = "SCOUT"  # Reconnaissance, detection, fire tracking
FIREFIGHTING: Final[str] = "FIREFIGHTING"  # Water / retardant delivery

# Shared field capabilities
AERIAL_RESPONSE: Final[str] = "AERIAL_RESPONSE"  # Generic aerial responder (legacy)
RECEIVE_COMMANDS: Final[str] = "RECEIVE_COMMANDS"  # Accepts inbound Command payloads
HEARTBEAT: Final[str] = "HEARTBEAT"  # Publishes periodic heartbeat
TELEMETRY: Final[str] = "TELEMETRY"  # Publishes telemetry data
