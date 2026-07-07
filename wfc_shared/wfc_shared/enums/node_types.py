"""
wfc_shared.enums.node_types
============================
Canonical node type identifiers.
Every node announces its type in NodeAnnouncement.node_type.
Used by: NodeRegistry queries, RuleEngine rules, BackupCommander failover.
"""
from __future__ import annotations

from typing import Final

# Commander tier
CENTRAL_COMMANDER: Final[str] = "CENTRAL_COMMANDER"   # Active rule-engine commander
BACKUP_COMMANDER: Final[str] = "BACKUP_COMMANDER"    # Hot-standby; promotes on lease expiry

# Swarm tier
SWARM_LEADER: Final[str] = "SWARM_LEADER"        # Tactical brain; one active per zone

# Drone tier
SCOUT_DRONE: Final[str] = "SCOUT_DRONE"         # Reconnaissance, detection, tracking
FIREFIGHTING_DRONE: Final[str] = "FIREFIGHTING_DRONE"  # Water / retardant delivery

# Support nodes
OPERATOR_HMI: Final[str] = "OPERATOR_HMI"        # Human dashboard; approval authority
INFRA_NODE: Final[str] = "INFRA_NODE"          # Infrastructure / gateway nodes
VIRTUAL: Final[str] = "VIRTUAL"             # Simulation / test nodes only

# Deprecated
DRONE_NODE: Final[str] = "DRONE_NODE"          # DEPRECATED - use SCOUT_DRONE
                                           # or FIREFIGHTING_DRONE
