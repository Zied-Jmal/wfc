"""mqtt_client.py
MQTTClient - publish / subscribe wrapper over paho-mqtt
- Manage MQTT connection lifecycle
- Publish and subscribe to topics
- Register LWT before connecting (set_will)
- Dispatch incoming messages to registered handlers
"""

from __future__ import annotations

# Standard Library

import json
from typing import Any
from collections.abc import Callable

# Third-Party Libraries

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion as MQTTCallbackAPIVersion
from paho.mqtt.client import ConnectFlags, DisconnectFlags
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

# Project Imports

from core.utils.config import get_mqtt_host, get_mqtt_port

Handler = Callable[[str, Any], None]

# region  CLASS - MQTTClient

class MQTTClient:

    """
    MQTT wrapper handling connection, pub/sub,
    message dispatching, and LWT registration.
    """

    # region  INITIALISATION

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id
        self._connected = False
        self.client     = mqtt.Client(
            client_id=client_id,
            callback_api_version=MQTTCallbackAPIVersion.VERSION2,
        )
        self.host = get_mqtt_host()
        self.port = get_mqtt_port()
        self._handler:               Handler | None = None
# dict of topic -> qos so resubscribe-on-reconnect
# preserves the QoS level each topic was originally subscribed at.
        self._subscribed_topics: dict[str, int] = {}   # topic -> QoS

# Enable automatic reconnect (1s min, 60s max backoff)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_message    = self._on_message
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    # endregion

    # region  CONNECTION MANAGEMENT

    def set_will(self, topic: str, payload: dict[str, Any] | str) -> None:
        """Register a Last Will and Testament message.

        MUST be called before connect() - the broker needs it at handshake time.
        The broker publishes this automatically on unexpected disconnect.
        """
        data = json.dumps(payload) if isinstance(payload, dict) else payload
        self.client.will_set(topic, data, qos=1, retain=True)

    def connect(self, retry_interval: int = 5, max_retries: int = -1) -> None:
        """
        Connect to the MQTT broker with automatic retry on failure.

        Args:
            retry_interval: Seconds to wait between retry attempts.
            max_retries: Maximum retries (-1 = infinite until success).
        """
        import time
        attempt = 0
        while True:
            try:
                self.client.connect(self.host, self.port)
                self.client.loop_start()
                return  # Success
            except Exception as e:
                if max_retries != -1 and attempt >= max_retries:
                    raise RuntimeError(
                        f"Failed to connect to MQTT broker after {attempt} attempts"
                    ) from e
                attempt += 1
                print(f"⚠️  MQTT connection failed (attempt {attempt}): {e}")
                print(f"   Retrying in {retry_interval}s...")
                time.sleep(retry_interval)

    def disconnect(self) -> None:
        """Stop the network loop and disconnect from the broker cleanly."""
        self.client.loop_stop()
        self.client.disconnect()

    # endregion

    # region  PROPERTIES

    @property
    def connected(self) -> bool:
        """True while the client is connected to the broker."""
        return self._connected

    # endregion

    # region  PUB / SUB

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe to an MQTT topic. Stores topic (and its QoS) for
        re-subscription on reconnect.
        Pass qos=1 for topics where delivery matters.
        """
        existing_qos = self._subscribed_topics.get(topic)
        if existing_qos is None or qos > existing_qos:
            self._subscribed_topics[topic] = qos

        if self._connected:
            self.client.subscribe(topic, qos=self._subscribed_topics[topic])
# If not connected, it will be subscribed in _on_connect

    def publish(self, topic: str, payload: dict[Any, Any] | str, qos: int = 0) -> None:
        """Publish a message. Dicts are JSON-serialised automatically.
        Pass qos=1 for at-least-once delivery on critical topics.
        """
        data = json.dumps(payload) if isinstance(payload, dict) else payload
        self.client.publish(topic, data, qos=qos)

    def publish_retained(self, topic: str, payload: dict[Any, Any] | str, qos: int = 0) -> None:
        """Publish a retained message. Retained messages are re-delivered to
        any new subscriber immediately on connection.
        """
        data = json.dumps(payload) if isinstance(payload, dict) else payload
        self.client.publish(topic, data, qos=qos, retain=True)

    # endregion

    # region  HANDLER SYSTEM

    def set_handler(self, fn: Handler) -> None:
        """Register a message handler. Multiple calls chain handlers."""
        if self._handler is None:
            self._handler = fn
        else:
            _prev = self._handler
            def _chained(t: str, p: Any) -> None:
                _prev(t, p)
                fn(t, p)
            self._handler = _chained

    # endregion

    # region  PRIVATE - MQTT CALLBACKS

# CHANGE 4: Update _on_connect to resubscribe ALL topics
    def _on_connect(
        self,
        client:     mqtt.Client,
        userdata:   Any,
        flags:      ConnectFlags,
        rc:         ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        self._connected = (rc == 0)
        if self._connected:
            # Resubscribe to ALL topics on every reconnect, each at its
            # originally-requested QoS (uses dict topic->qos, was a
            # plain list that always resubscribed at QoS 0).
            # Use a list copy to avoid 'dictionary changed size during iteration'
            for topic, qos in list(self._subscribed_topics.items()):
                self.client.subscribe(topic, qos=qos)

    def _on_disconnect(
        self,
        client:           mqtt.Client,
        userdata:         Any,
        disconnect_flags: DisconnectFlags,
        reason_code:      ReasonCode,
        properties:       Properties | None = None,
    ) -> None:
        self._connected = False

    def _on_message(
        self,
        client:   mqtt.Client,
        userdata: Any,
        msg:      mqtt.MQTTMessage,
    ) -> None:
        try:
            payload = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            payload = msg.payload.decode()
        if self._handler:
            self._handler(msg.topic, payload)

    # endregion

# endregion (end of class MQTTClient)
