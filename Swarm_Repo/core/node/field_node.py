"""field_node.py
FieldNode - base for all swarm field-responder nodes
- Subscribe to own command topic on start
- Handle full ACK protocol (RECEIVED EXECUTED)
- Dispatch all commanderleader commands to subclass
via _execute_command()
- Deduplicate by trace_id
- Route registry announces to _on_registry_announce()
What subclasses must implement:
_execute_command(command_type, fire_payload, trace_id)
What subclasses do NOT need to worry about:
- MQTT subscription
- trace_id parsing / validation / dedup
- ACK publishing
- Heartbeat / LWT / announce (BaseNode handles)
"""

from __future__ import annotations

import time
from abc import abstractmethod
from typing import Any, Final

from core.node.base_node import BaseNode
from core.utils.logger import log
from wfc_shared.enums.command_types import (
    COMMANDER_TO_LEADER_COMMANDS,
    LEADER_TO_DRONE_COMMANDS,
)
from wfc_shared.enums.topics import (
    ACK,
    command_topic,
)

# and drone nodes (receive leader commands). The old whitelist was
# COMMANDER_TO_LEADER_COMMANDS only - drone commands (DISPATCH_DRONE etc.)
# were silently rejected and ACKed as EXECUTED without being executed.
_ALL_KNOWN_COMMANDS: Final = COMMANDER_TO_LEADER_COMMANDS | LEADER_TO_DRONE_COMMANDS

# ACK status values (wire)
_RECEIVED: Final = "RECEIVED"
_EXECUTED: Final = "EXECUTED"
_FAILED: Final = "FAILED"

# Inline mapping: status COMMAND_* event_type
# Identical to WFC main repo's lifecycle_rules.STATUS_TO_EVENT_TYPE.
# Duplicated here so swarm repo has no dependency on the
# commander's commands_monitor module.
_STATUS_TO_EVENT_TYPE: Final[dict[str, str]] = {
    "RECEIVED": "COMMAND_RECEIVED",
    "EXECUTED": "COMMAND_EXECUTED",
    "FAILED": "COMMAND_FAILED",
}


class FieldNode(BaseNode):
    """
    Lightweight base for all swarm field-responder nodes.
    Concrete subclasses implement _execute_command() only.
    """

    # INITIALISATION

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # : dedup by trace_id - see WFC main field_node.py
        # for full rationale. Safe because paho loop_start() processes
        # on_message serially in one background thread.
        self._handled_trace_ids: set[str] = set()

    # LIFECYCLE

    def start(self) -> None:
        super().start()
        # qos=1 - must match dispatcher's qos=1 publish (QoS is MIN
        # of publisher+subscriber; qos=0 here silently downgrades
        # every command to fire-and-forget).
        self.mqtt.subscribe(command_topic(self.node_id), qos=1)
        log(
            self.__class__.__name__,
            f"{self.node_id} ready - zone={self.zone} location={self.location} caps={self.capabilities}",
            channel="SYSTEM",
        )

    # MESSAGE ROUTING

    def handle_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        """
        Route incoming messages to command handler or registry announce hook.

        Args:
            topic: MQTT topic.
            payload: Parsed message payload.

        Returns:
            None
        """
        if not isinstance(payload, dict):
            return

        if topic == command_topic(self.node_id):
            self._handle_command(payload)
            return

        # Registry announces - dispatch to override hook
        if topic.startswith("wfc/registry/announce/"):
            self._on_registry_announce(payload)
            return

    # COMMAND PROTOCOL

    def _handle_command(self, payload: dict[str, Any]) -> None:
        """Process and ACK an incoming command with trace_id dedup."""
        trace_id = payload.get("trace_id")
        command_type = payload.get("command_type")

        if not trace_id:
            log(self.__class__.__name__, "command missing trace_id - skipped", channel="SYSTEM")
            return

        # already-handled trace_id: re-ACK as EXECUTED and skip.
        if trace_id in self._handled_trace_ids:
            log(
                self.__class__.__name__,
                f"duplicate command trace={trace_id[:8]} - already handled, skipped",
                channel="SYSTEM",
            )
            self._send_ack(trace_id, _EXECUTED)
            return

        log(self.__class__.__name__, f"received {command_type} trace={trace_id[:8]}", channel="SYSTEM")

        self._send_ack(trace_id, _RECEIVED)

        if command_type not in _ALL_KNOWN_COMMANDS:
            log(self.__class__.__name__, f"unknown command type: {command_type} - skipped", channel="SYSTEM")
            # Still ACK as EXECUTED so the tracker doesn't hang
            self._send_ack(trace_id, _EXECUTED)
            return

        try:
            fire_payload = payload.get("payload", {})
            self._execute_command(command_type, fire_payload, trace_id)  # pyright: ignore[reportArgumentType]
            self._handled_trace_ids.add(trace_id)
            self._send_ack(trace_id, _EXECUTED)
        except Exception as exc:
            log(self.__class__.__name__, f"command execution failed: {exc}", channel="SYSTEM")
            self._send_ack(trace_id, _FAILED)

    # SUBCLASS CONTRACT

    @abstractmethod
    def _execute_command(
        self,
        command_type: str,
        fire_payload: dict[str, Any],
        trace_id: str,
    ) -> None:
        """
        Execute one command. Called after RECEIVED ack, before EXECUTED ack.

        Args:
            command_type: The type identifier of the command.
            fire_payload: Command-specific payload data.
            trace_id: Unique trace identifier for ACK correlation.

        Raises:
            Exception: Any exception triggers a FAILED ACK.

        Returns:
            None
        """
        ...

    def _on_registry_announce(self, payload: dict[str, Any]) -> None:
        """
        Override in subclasses to react to registry announce messages.

        Args:
            payload: The registry announcement payload.

        Returns:
            None
        """
        pass

    # PRIVATE HELPERS

    def _send_ack(self, trace_id: str, status: str) -> None:
        """Publish an ACK with event_type and event_id for CommandTracker."""
        event_type = _STATUS_TO_EVENT_TYPE.get(status, status)
        event_id = f"{trace_id}:{status}"
        self.mqtt.publish(
            ACK,
            {
                "trace_id": trace_id,
                "node_id": self.node_id,
                "status": status,
                "event_type": event_type,
                "event_id": event_id,
                "timestamp": time.time(),
            },
            qos=1,
        )
        log(self.__class__.__name__, f"ACK {status} trace={trace_id[:8]}", channel="SYSTEM")
