"""FieldNode - base class for all field responder nodes.
Concrete subclasses implement _execute_command().
The node has no local state beyond what BaseNode provides -
no rule engine, no approval pipeline, no registry bridge.
It is purely a command receiver and ACK publisher.
"""

from __future__ import annotations

import time
from abc import abstractmethod
from typing import Any, Final

from core.commands_monitor.lifecycle_rules import STATUS_TO_EVENT_TYPE
from core.node.base_node import BaseNode
from core.utils.logger import log
from wfc_shared.enums.command_types import CONTAIN_FIRE, RESPOND_TO_FIRE, STAND_DOWN
from wfc_shared.enums.topics import ACK, command_topic

# ack status values (human-readable, used in logs/dashboard)
_RECEIVED: Final = "RECEIVED"
_EXECUTED: Final = "EXECUTED"
_FAILED: Final = "FAILED"


# region  CLASS - FieldNode


class FieldNode(BaseNode):
    """Lightweight base for all field responder nodes (swarm leaders,
    drones, infra nodes). Concrete subclasses implement
    _execute_command() and nothing else is required.

    The node has no local state beyond what BaseNode provides -
    no rule engine, no approval pipeline, no registry bridge.
    It is purely a command receiver and ACK publisher.
    """

    # region  INITIALISATION

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # dedup set - trace_ids already handled this process lifetime
        self._handled_trace_ids: set[str] = set()

    # endregion

    # region  LIFECYCLE

    def start(self) -> None:
        super().start()
        # qos=1 - must match the dispatcher's qos=1 publish for
        # at-least-once delivery to actually take effect (QoS is the
        # MIN of publisher and subscriber; subscribing at 0 here would
        # silently downgrade every command back to fire-and-forget).
        self.mqtt.subscribe(command_topic(self.node_id), qos=1)
        log(
            self.__class__.__name__,
            f"{self.node_id} ready - zone={self.zone} location={self.location} caps={self.capabilities}",
            channel="SYSTEM",
        )

    # endregion

    # region  MESSAGE ROUTING

    def handle_message(self, topic: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if topic == command_topic(self.node_id):
            self._handle_command(payload)  # pyright: ignore[reportUnknownArgumentType]

    # endregion

    # region  COMMAND PROTOCOL

    def _handle_command(self, payload: dict[str, Any]) -> None:
        trace_id = payload.get("trace_id")
        command_type = payload.get("command_type")

        if not trace_id:
            log(self.__class__.__name__, "command missing trace_id - skipped", channel="SYSTEM")
            return

        # dedup - Checked BEFORE execution but only ADDED to the set on success (below) - a command
        # that previously FAILED is deliberately still retryable; only
        # a confirmed-EXECUTED trace_id is treated as already-handled.
        # Safe against a true execute-twice race specifically because
        # MQTTClient runs paho's loop_start() - one background thread
        # processes on_message serially, so two arrivals of the same
        # trace_id are always handled one at a time here, never
        # concurrently. If this class is ever driven by a different,
        # multi-threaded message loop, this check would need a lock.
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

        known = {RESPOND_TO_FIRE, STAND_DOWN, CONTAIN_FIRE}
        if command_type not in known:
            log(self.__class__.__name__, f"unknown command type: {command_type} - skipped", channel="SYSTEM")
            # Still send EXECUTED so the tracker doesn't hang waiting
            self._send_ack(trace_id, _EXECUTED)
            return

        try:
            fire_payload = payload.get("payload", {})
            self._execute_command(command_type, fire_payload, trace_id)
            self._handled_trace_ids.add(trace_id)
            self._send_ack(trace_id, _EXECUTED)
        except Exception as exc:
            log(self.__class__.__name__, f"command execution failed: {exc}", channel="SYSTEM")
            self._send_ack(trace_id, _FAILED)

    # endregion

    # region  SUBCLASS CONTRACT

    @abstractmethod
    def _execute_command(
        self,
        command_type: str,
        fire_payload: dict[str, Any],
        trace_id: str,
    ) -> None:
        """Execute one command. Called after RECEIVED ack, before EXECUTED ack.

        Args:
            command_type  : RESPOND_TO_FIRE | STAND_DOWN | CONTAIN_FIRE
            fire_payload  : inner payload dict - fire_id, location, severity, reason, …
            trace_id      : command trace for logging

        Raise any exception to trigger a FAILED ack.
        """
        ...

    # endregion

    # region  PRIVATE HELPERS

    def _send_ack(self, trace_id: str, status: str) -> None:
        """Publish an ACK for trace_id.
        Includes event_type and event_id which CommandTracker.update()
        requires to accept the event. event_id is deterministic,
        derived from (trace_id, status) - same (trace_id, status)
        always produces the same event_id, so retransmissions are
        correctly deduped by the tracker.
        """
        event_type = STATUS_TO_EVENT_TYPE.get(status, status)
        event_id = f"{trace_id}:{status}"
        # qos=1 - an ACK is what tells the commander a command
        # actually landed; losing it silently is what caused commands
        # to get stuck at ISSUED forever.
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

    # endregion


# endregion (end of class FieldNode)
