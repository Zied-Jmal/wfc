"""registry_bridge.py
RegistryBridge - MQTT NodeRegistry synchronisation
- Subscribe to wfc/registry/announce/# (per-node)
- Register ONLINE nodes in the local NodeRegistry
- Mark OFFLINE nodes as DEAD (LWT or graceful stop)
"""

from __future__ import annotations

# Standard Library

from typing import Any

# Third-Party Libraries

# Project Imports

from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.enums.topics import REGISTRY_ANNOUNCE_WILDCARD, REGISTRY_ANNOUNCE_TARGET
from core.utils.logger import log
from core.messaging.mqtt_client import MQTTClient
from core.node_registry.registry import NodeRegistry

# prefix used to recognize ANY per-node announce topic.
# REGISTRY_ANNOUNCE_TARGET is "wfc/registry/announce/{node_id}" -
# splitting on "{node_id}" gives us everything before the
# placeholder, i.e. "wfc/registry/announce/".
_ANNOUNCE_PREFIX = REGISTRY_ANNOUNCE_TARGET.split("{node_id}")[0]
# region  CLASS - RegistryBridge

class RegistryBridge:

    """Wires the MQTT announcement topic to the local NodeRegistry.

    On ONLINE announcement registry.register()
    On OFFLINE announcement registry.mark_dead()
    register() is idempotent - duplicate ONLINE messages are silent no-ops.
    """

    # region  INITIALISATION

    def __init__(self, mqtt_client:MQTTClient , registry: NodeRegistry ) -> None:
        self._mqtt :MQTTClient    = mqtt_client
        self._registry:NodeRegistry = registry

    # endregion

    # region  PUBLIC API

    def start(self) -> None:
        """Subscribe to the announce wildcard.
        Subscribes at qos=1 to match the qos=1 used by every announce
        publish.
        """
        self._mqtt.subscribe(REGISTRY_ANNOUNCE_WILDCARD, qos=1)
        log("RegistryBridge", f"subscribed to {REGISTRY_ANNOUNCE_WILDCARD}", channel="REGISTRY")

    # endregion

    # region  PUBLIC API - message handling

    def on_message(self, topic: str, payload: dict[Any, Any] | str) -> None:
        """Process one MQTT message if it's a registry announcement.
        Called explicitly by CommanderCore's handle_message() rather
        than being wired as its own MQTT handler. No-ops on anything
        that isn't a registry announce topic.
        """
        self._on_message(topic, payload)

    # region  PRIVATE METHODS

    def _on_message(self, topic: str, payload: dict[Any, Any] | str) -> None:
        """Matches by PREFIX (topic.startswith(_ANNOUNCE_PREFIX)) instead
        of exact equality, since announcements now arrive on per-node
        topics (wfc/registry/announce/{node_id}).
        """
        if not topic.startswith(_ANNOUNCE_PREFIX):
            return
        if not isinstance(payload, dict):
            log("RegistryBridge", "non-dict payload on announce topic - dropped",
                channel="REGISTRY")
            return
        try:
            ann = NodeAnnouncement(**payload)

            if ann.status == "OFFLINE":
                self._registry.mark_offline(ann.node_id)
                log("RegistryBridge",
                    f"node offline ({ann.node_id}) - marked DEAD",
                    channel="REGISTRY")
                return

            self._registry.register(
                node_id=ann.node_id,
                node_type=ann.node_type,
                capabilities=ann.capabilities,  # pyright: ignore[reportArgumentType]
                zone=ann.zone,
                location=ann.location,
            )
            log("RegistryBridge",
                f"registered {ann.node_id} ({ann.node_type}) "
                f"caps={ann.capabilities} zone={ann.zone} location={ann.location}",
                channel="REGISTRY")

        except Exception as exc:
            log("RegistryBridge", f"failed to process announcement: {exc}",
                channel="REGISTRY")

    # endregion

# endregion (end of class RegistryBridge)
