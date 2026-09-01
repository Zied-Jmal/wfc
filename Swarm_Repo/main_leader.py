#!/usr/bin/env python3
# main_leader.py - Entrypoint for SwarmLeaderNode
# Leader itself has no physical engine (stationary ground node).
# NODE_LOCATION is still passed to FieldNode.location for the
# registry announce so the dashboard can plot it on the map.
# ENV VARS:
#   NODE_ID                 : leader node id (e.g. sl-A-01)
#   NODE_ZONE               : zone (e.g. zone_alpha)
#   NODE_LOCATION           : "lat_deg,lon_deg"  WGS-84
#   HOME_ALT_M              : altitude m AMSL (default 50.0)
#   IS_BACKUP               : true|false (default false)
#   BACKUP_PEERS            : comma-separated peer leader node_ids
#   SWARM_STATUS_INTERVAL   : seconds between SwarmStatusSnapshot (default 10)
#   LEADER_HEARTBEAT_TIMEOUT: seconds before a leader is considered lost (default 10)
#   ELECTION_TIMEOUT        : seconds for election to complete (default 5)
#   MQTT_HOST / MQTT_PORT   : broker connection
from __future__ import annotations

import os
import signal
import sys
from types import FrameType

from core.node.swarm_leader_node import SwarmLeaderNode
from core.utils.config import (
    get_backup_peers,
    get_is_backup,
    get_node_id,
    get_node_location,
    get_node_zone,
)


def main() -> None:
    """Entrypoint for SwarmLeaderNode - starts the leader (or backup leader) node."""

    node_id = get_node_id()
    zone = get_node_zone() or "zone_alpha"
    # Leader uses (lat, lon) tuple for FieldNode.location - WGS-84
    location = get_node_location() or (36.8065, 10.1815)

    node = SwarmLeaderNode(
        node_id=node_id,
        zone=zone,
        location=location,
        backup_peers=get_backup_peers(),
        is_backup=get_is_backup(),
    )

    def _shutdown(sig: int | None, frame: FrameType | None) -> None:
        print(f"\n[{node_id}] shutting down…", flush=True)
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    node.start()

    role = "BACKUP" if get_is_backup() else "ACTIVE"
    print(
        f"[{node_id}] LEADER ({role}) started\n"
        f"  zone     : {zone}\n"
        f"  location : {location[0]:.5f}°N {location[1]:.5f}°E\n"
        f"  peers    : {get_backup_peers()}\n"
        f"  broker   : {os.getenv('MQTT_HOST', 'localhost')}:{os.getenv('MQTT_PORT', '1883')}",
        flush=True,
    )

    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(None, None)


if __name__ == "__main__":
    main()
