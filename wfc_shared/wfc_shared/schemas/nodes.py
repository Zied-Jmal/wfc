"""
wfc_shared.schemas.nodes
==========================
NodeRecord - typed representation of a node in the commander's registry.
Used by: NodeRegistry (commander-side), RuleEngine queries.
Frozen: mutations go through model_copy(update=...) inside NodeRegistry only.
"""

from __future__ import annotations

import time
from pydantic import BaseModel, Field, ConfigDict
from wfc_shared.enums.node_status import REGISTERED


class NodeRecord(BaseModel):
    """
    Immutable snapshot of a node's known state in the registry.

    Field notes
    -----------
    node_id       : globally unique identifier
    node_type     : from wfc_shared.enums.node_types
    capabilities  : from wfc_shared.enums.capabilities
    status        : UNREGISTERED | REGISTERED | ACTIVE | OFFLINE
    last_seen     : UNIX epoch of last heartbeat
    registered_at : UNIX epoch when node first announced
    zone          : zone label (matches FirePayload.zone)
    location      : (lat_deg, lon_deg) WGS-84 for distance math
    current_job   : fire_id when node has active assignment; None = idle
    

"""
    model_config = ConfigDict(frozen=True)

    node_id:       str
    node_type:     str
    capabilities:  list[str]                         = Field(default_factory=list)
    status:        str                                = REGISTERED
    last_seen:     float | None                      = None
    registered_at: float                              = Field(default_factory=time.time)
    zone:          str | None                        = None
    location:      tuple[float, float] | None        = None  # (lat_deg, lon_deg) WGS-84
    current_job:   str | None                        = None  # fire_id or None
