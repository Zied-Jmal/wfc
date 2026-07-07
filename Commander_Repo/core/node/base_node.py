"""base_node.py
BaseNode - abstract base class for all node types
- Bootstrap MQTT connection with LWT before connecting
- Publish retained announcement on start / stop
- Run heartbeat publisher and heartbeat monitor
- Dispatch incoming MQTT messages to handle_message()
- Re-publish a node's registry status on behalf of
CentralNode / BackupCommander (shared helper)
- Provide override hooks for subclasses
NodeRegistry, NodeAnnouncement, config, logger
InfraNode, FieldNode
"""

from __future__ import annotations

import time
from typing import Any

from core.messaging.mqtt_client import MQTTClient
from core.heartbeat.heartbeat import Heartbeat
from core.heartbeat.monitor import HeartbeatMonitor
from core.node_registry.registry import NodeRegistry
from core.persistence.database import get_db  # pyright: ignore[reportUnknownVariableType]
from core.utils.config import get_db_path
from core.utils.logger import log
from wfc_shared.enums.topics import WFC_ALL, registry_announce_topic
from wfc_shared.schemas.announcements import NodeAnnouncement


# region  CLASS - BaseNode

class BaseNode:

    """Abstract base class for all node types in the WFC system.
    Subclasses override handle_message(), _on_node_failed(),
    and _on_node_recovered() to implement their own logic.
    """

    # region  INITIALISATION

    def __init__(
        self,
        node_id:      str,
        node_type:    str,
        capabilities: list[str]                  | None = None,
        zone:         str                        | None = None,
        location:     tuple[float, float]        | None = None,
    ):
        self.node_id      = node_id
        self.node_type    = node_type
        self.capabilities = capabilities or []
        self.zone         = zone
        self.location     = location

        self.db        = get_db(get_db_path())
        self.registry  = NodeRegistry(db=self.db)
        self.mqtt      = MQTTClient(client_id=node_id)
        self.heartbeat = Heartbeat(interval=5)
        self.monitor   = HeartbeatMonitor(
            timeout=10,
            registry=self.registry,
            on_node_failed=self._on_node_failed,
            on_node_recovered=self._on_node_recovered,
        )

    # endregion

    # region  LIFECYCLE

    def start(self) -> None:
        """Connect to MQTT, start heartbeat and monitor, announce presence."""
        self.mqtt.set_will(
            topic=registry_announce_topic(self.node_id),
            payload=self._build_announcement("OFFLINE").model_dump(),
        )
        self.mqtt.connect()
        self.mqtt.subscribe(WFC_ALL)
        self.mqtt.set_handler(self._on_message)
        self.monitor.start()
        self.heartbeat.start(self._send_heartbeat)
        self._announce()
        log("BaseNode", f"{self.node_id} started", channel="SYSTEM")

    def stop(self) -> None:
        """Gracefully stop - publish OFFLINE before disconnecting."""
        self.heartbeat.stop()
        self.monitor.stop()
        # qos=1 - a lost OFFLINE announcement means other
        # nodes never learn this one left.
        self.mqtt.publish_retained(
            registry_announce_topic(self.node_id),
            self._build_announcement("OFFLINE").model_dump(),
            qos=1,
        )
        self.mqtt.disconnect()
        log("BaseNode", f"{self.node_id} stopped", channel="SYSTEM")

    # endregion

    # region  OVERRIDE IN SUBCLASSES

    def handle_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        """Override to handle incoming MQTT messages."""
        pass

    def _on_node_failed(self, payload: dict[str, Any]) -> None:
        """Override to react when a monitored node goes DEAD."""
        pass

    def _on_node_recovered(self, payload: dict[str, Any]) -> None:
        """Override to react when a monitored node comes back ALIVE."""
        pass

    # endregion

    # region  SHARED HELPERS (used by subclasses)

    def _announce_node_status(self, node_id: str, status: str) -> None:
        """Re-publish a retained registry announcement for `node_id`
        with the given status (ONLINE/OFFLINE).

        HeartbeatMonitor only updates this node's local NodeRegistry
        on a timeout/recovery - it never touches MQTT. Without this,
        a hung (not disconnected) node never gets marked DEAD on
        wfc/registry/announce, so other nodes and the dashboard never
        learn about the failure. We are the authority that detected
        it, so we broadcast the decision using the failed node's own
        last-known identity.

        Moved to BaseNode from CentralNode and BackupCommander.

        Publishes to node_id's OWN topic slot
        (registry_announce_topic(node_id)), not this node's. This is
        publishing ON BEHALF OF node_id (e.g. Backup declaring Central
        dead) - it must land in node_id's retained slot so a later
        subscriber sees node_id's correct last-known status.
        """
        record = self.registry.get(node_id)
        if record is None:
            return
        announcement = NodeAnnouncement(
            node_id=record.node_id,
            node_type=record.node_type,  # pyright: ignore[reportArgumentType]
            capabilities=record.capabilities,  # pyright: ignore[reportArgumentType]
            status=status,  # pyright: ignore[reportArgumentType]
            zone=record.zone,
            location=record.location,
        )
        self.mqtt.publish_retained(registry_announce_topic(node_id), announcement.model_dump(), qos=1)

    # endregion

    # region  PRIVATE METHODS

    def _build_announcement(self, status: str) -> NodeAnnouncement:
        return NodeAnnouncement(
            node_id=self.node_id,
            node_type=self.node_type,  # pyright: ignore[reportArgumentType]
            capabilities=self.capabilities,  # pyright: ignore[reportArgumentType]
            zone=self.zone,
            location=self.location,
            status=status,  # pyright: ignore[reportArgumentType]
        )

    def _announce(self) -> None:
        """Publish a retained ONLINE announcement so late-joining nodes
        discover this node immediately on subscribe.
        Publishes to this node's OWN topic slot
        (registry_announce_topic(self.node_id)).
        """
        self.mqtt.publish_retained(
            registry_announce_topic(self.node_id),
            self._build_announcement("ONLINE").model_dump(),
            qos=1,
        )
        log(
            "BaseNode",
            f"announced {self.node_id} ({self.node_type}) "
            f"caps={self.capabilities} zone={self.zone} location={self.location}",
            channel="REGISTRY",
        )

    def _send_heartbeat(self) -> None:
        self.mqtt.publish(f"wfc/nodes/{self.node_id}/heartbeat", {
            "node_id":   self.node_id,
            "type":      self.node_type,
            "timestamp": time.time(),
            "status":    "alive",
        })

    def _on_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        if "heartbeat" in topic and isinstance(payload, dict):
            node_id = payload.get("node_id")
            if node_id:
                self.monitor.update(node_id)
        self.handle_message(topic, payload)

    # endregion

# endregion (end of class BaseNode)
