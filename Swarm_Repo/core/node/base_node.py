"""base_node.py
BaseNode - abstract base class for all swarm node types
- Bootstrap MQTT connection with LWT before connecting
- Publish retained announcement on start / stop
- Run heartbeat publisher (5s)
- Dispatch incoming MQTT messages to handle_message()
- Provide override hooks for subclasses
Does NOT have: DB, NodeRegistry, approval pipeline,
rule engine, or command dispatch.
FirefightingDroneNode
"""

from __future__ import annotations

import time
from typing import Any

from core.heartbeat.heartbeat import Heartbeat
from core.messaging.mqtt_client import MQTTClient
from core.utils.logger import log
from wfc_shared.enums.topics import (
    REGISTRY_ANNOUNCE_WILDCARD,
    heartbeat_topic,  # was manually formatting the topic string
    registry_announce_topic,
)
from wfc_shared.schemas.announcements import NodeAnnouncement


class BaseNode:
    """
    Abstract base class for all node types in the WFC Swarm repo.
    Subclasses override handle_message() to implement their logic.
    """

    def __init__(
        self,
        node_id: str,
        node_type: str,
        capabilities: list[str] | None = None,
        zone: str | None = None,
        location: tuple[float, float] | None = None,
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.capabilities = capabilities or []
        self.zone = zone
        self.location = location

        self.mqtt = MQTTClient(client_id=node_id)
        self.heartbeat = Heartbeat(interval=5)

    def start(self) -> None:
        """
        Connect to MQTT, start heartbeat, and announce presence.

        Args:
            None

        Returns:
            None
        """
        self.mqtt.set_will(
            topic=registry_announce_topic(self.node_id),
            payload=self._build_announcement("OFFLINE").model_dump(),
        )
        self.mqtt.connect()
        # All nodes listen to registry announces so they can discover each other
        self.mqtt.subscribe(REGISTRY_ANNOUNCE_WILDCARD, qos=1)
        self.mqtt.set_handler(self._on_message)
        self.heartbeat.start(self._send_heartbeat)
        self._announce()
        log("BaseNode", f"{self.node_id} started", channel="SYSTEM")

    def stop(self) -> None:
        """
        Gracefully stop the node - publish OFFLINE before disconnecting.

        Args:
            None

        Returns:
            None
        """
        self.heartbeat.stop()
        self.mqtt.publish_retained(
            registry_announce_topic(self.node_id),
            self._build_announcement("OFFLINE").model_dump(),
            qos=1,
        )
        self.mqtt.disconnect()
        log("BaseNode", f"{self.node_id} stopped", channel="SYSTEM")

    def handle_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        """
        Override to handle incoming MQTT messages.

        Args:
            topic: MQTT topic the message was received on.
            payload: Parsed message payload (dict for JSON, str for raw).

        Returns:
            None
        """
        pass

    def _build_announcement(self, status: str) -> NodeAnnouncement:
        """Build a NodeAnnouncement with the current node state."""
        return NodeAnnouncement(
            node_id=self.node_id,
            node_type=self.node_type,  # pyright: ignore[reportArgumentType]
            capabilities=self.capabilities,  # pyright: ignore[reportArgumentType]
            zone=self.zone,
            location=self.location,
            status=status,  # pyright: ignore[reportArgumentType]
        )

    def _announce(self) -> None:
        """Publish retained ONLINE announcement for late-joining node discovery."""
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
        """Publish a heartbeat message on the heartbeat topic."""
        self.mqtt.publish(
            heartbeat_topic(self.node_id),
            {
                "node_id": self.node_id,
                "type": self.node_type,
                "timestamp": time.time(),
                "status": "alive",
            },
        )

    def _on_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        """Dispatch incoming MQTT message to handle_message."""
        self.handle_message(topic, payload)
