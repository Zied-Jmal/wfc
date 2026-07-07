"""command_dispatcher.py
CommandDispatcher - command publish + tracker registration
- Assign a trace_id to every outgoing command
- Register the command in CommandTracker
- Publish the command directly over MQTT
to wfc/command/{target_node}
"""

from __future__ import annotations

import time
import uuid

from wfc_shared.enums.topics import command_topic
from core.messaging.mqtt_client import MQTTClient
from core.commands_monitor.command_tracker import CommandTracker
from core.utils.logger import log

# command types that are emitted at most once per fire by a
# single owning rule (see RuleEngine rules: FireDispatchRule,
# FireContainedRule, FireSuppressedRule). Safe to key deterministically
# by (command_type, fire_id, target_node) - see module header.
_IDEMPOTENT_COMMAND_TYPES = {"RESPOND_TO_FIRE", "CONTAIN_FIRE", "STAND_DOWN"}

def _deterministic_trace_id(command_type: str, fire_id: str, target_node: str) -> str:
    """
    Derive a stable trace_id from (command_type, fire_id, target_node).

    Uses a UUID5 (name-based, deterministic) rather than UUID4
    (random) so the SAME logical command - wherever or whenever it's
    decided - always produces the SAME trace_id. This is what lets
    two independently-deciding processes (e.g. Central and Backup if
    ever both active) converge on one trace_id instead of issuing
    two for what's semantically the same dispatch.
    """
    key = f"{command_type}:{fire_id}:{target_node}"
    return str(uuid.uuid5(uuid.NAMESPACE_OID, key))

# region  CLASS - CommandDispatcher

class CommandDispatcher:    
    """Publishes commands over MQTT and registers them in CommandTracker.

    Assigns trace_id to every outgoing command, registers it in the
    tracker, and publishes to wfc/command/{target_node}.
    """

    # region  INITIALISATION

    def __init__(self, mqtt: MQTTClient, tracker: CommandTracker | None = None) -> None:
        """Initialize the command dispatcher.

        Args:
            mqtt: MQTTClient instance for publishing.
            tracker: Optional CommandTracker for ACK tracking.
        """
        self._mqtt    = mqtt
        self._tracker = tracker

    # endregion

    # region  PUBLIC API

    def send(self, command: Any) -> str:
        """Dispatch a command over MQTT and register it in the tracker.

        Idempotent command types (RESPOND_TO_FIRE, CONTAIN_FIRE, STAND_DOWN)
        get a deterministic trace_id derived from (command_type, fire_id,
        target_node). All others get a random UUID4.

        Args:
            command: Command envelope with target_node, command_type, payload.

        Returns:
            The trace_id assigned to this command.
        """
        command_type = command.command_type
        fire_id = getattr(command, "payload", {}).get("fire_id")

        if command_type in _IDEMPOTENT_COMMAND_TYPES and fire_id:
            trace_id = _deterministic_trace_id(command_type, fire_id, command.target_node)
        else:
            trace_id = str(uuid.uuid4())

        payload = {
            "trace_id":     trace_id,
            "command_id":   getattr(command, "command_id", str(uuid.uuid4())),
            "target_node":  command.target_node,
            "command_type": command.command_type,
            "payload":      getattr(command, "payload", {}),
            "timestamp":    time.time(),
        }

# Register in tracker before publishing so history starts clean.
# CommandTracker.create() is already idempotent (safe to call
# twice with the same trace_id).
        if self._tracker:  # pyright: ignore[reportUnknownMemberType]
            self._tracker.create(trace_id, payload)  # pyright: ignore[reportUnknownMemberType]

        self._mqtt.publish(command_topic(command.target_node), payload, qos=1)

        log("CommandDispatcher",
            f"sent {command.command_type} → {command.target_node} "
            f"trace={trace_id[:8]}",
            channel="COMMANDS")

        return trace_id

    # endregion

# endregion (end of class CommandDispatcher)
