"""node_runtime.py
CentralNode - primary command and control node
All commander-specific logic lives in CommanderCore. CentralNode:
1. Inherits BaseNode (MQTT, heartbeat, registry)
2. Owns a CommanderCore started in active=True mode
3. Delegates handle_message() to the core
4. Delegates node lifecycle callbacks to the core
"""

from __future__ import annotations
from typing import Any

from core.node.base_node import BaseNode
from core.persistence.repositories.alert_repo import AlertRepository
from command_nodes.core_commander.commander_core import CommanderCore
from wfc_shared.enums.capabilities import DISPATCH_COMMANDS, HEARTBEAT, SWARM_LEAD
from wfc_shared.enums.mission_status import PAUSED
from core.utils.logger import log
from core.utils.config import get_node_zone, get_node_location

# region  CLASS - CentralNode

class CentralNode(BaseNode):

    """
    Primary command and control node.

    All commander-specific logic lives in CommanderCore.
    This class wires BaseNode (transport/heartbeat/registry)
    with CommanderCore (rules/dispatch/state).
    """

    # region  INITIALISATION

    def __init__(self) -> None:
        super().__init__(
            node_id="central-commander",
            node_type="CENTRAL_COMMANDER",
            capabilities=[DISPATCH_COMMANDS, HEARTBEAT],
            zone=get_node_zone(),
            location=get_node_location(),
        )
# alerts kept here for _on_node_failed; core has its own copy too
        self._node_alerts = AlertRepository(db=self.db)

    # endregion

    # region  LIFECYCLE

    def start(self) -> None:
        """
        Start order:
            1. BaseNode.start() - MQTT, heartbeat, registry bridge
            2. CommanderCore.start(active=True) - all commander subsystems
        """

        self._core = CommanderCore(
            node_id=self.node_id,
            mqtt=self.mqtt,
            registry=self.registry,
            db=self.db,
        )
        super().start()

        self._core.start(active=True)

        log("CentralNode", "ready - CommanderCore active", channel="SYSTEM")

    def stop(self) -> None:
        self._core.stop()
        super().stop()

    # endregion

    # region  MESSAGE HANDLING - delegated

    def handle_message(self, topic: str, payload: Any) -> None:
        """Delegate all message routing to CommanderCore."""
        self._core.handle_message(topic, payload)

    # endregion

    # region  NODE LIFECYCLE CALLBACKS

    def _on_node_failed(self, payload: dict[str, Any]) -> None:
        node_id = payload.get("node_id")
        log("CentralNode", f"node OFFLINE: {node_id}", channel="SYSTEM")

        if node_id:
            rec = self.registry.get(node_id)
            self._announce_node_status(node_id, "OFFLINE")
            self._node_alerts.add(
                alert_id=f"node_down:{node_id}",
                kind="NODE_DOWN",
                severity="WARNING",
                title=f"Node offline: {node_id}",
                detail=f"reason={payload.get('reason', 'HEARTBEAT_TIMEOUT')}",
                source_ref=node_id,
            )

            if rec and rec.current_job and SWARM_LEAD in (rec.capabilities or []):
                fire_id = rec.current_job
                log("CentralNode",
                    f"swarm leader {node_id} was handling fire={fire_id[:8]} - re-dispatching",
                    channel="SYSTEM")
                self.registry.release_job(node_id)

                mission = self._core.missions.get_for_fire(fire_id)
                if mission:
                    self._core.missions.transition(
                        mission.mission_id, PAUSED,
                        reason=f"leader_{node_id}_offline"
                    )
                self._core.redispatch_fire(fire_id, dead_leader=node_id)

    def _on_node_recovered(self, payload: dict[str, Any]) -> None:
        node_id = payload.get("node_id")
        log("CentralNode", f"node recovered: {node_id}", channel="SYSTEM")
        if node_id:
            self._announce_node_status(node_id, "ONLINE")

    # endregion

# endregion (end of class CentralNode)
