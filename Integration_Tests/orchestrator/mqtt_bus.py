# orchestrator/mqtt_bus.py
# Thin MQTT wrapper used by the test orchestrator.
# Always subscribes to wfc/# (it needs full visibility to verify ANY stage),
# and fans out every received message to a list of registered listeners.
# Scenario stages register/unregister listeners as they run.

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Final

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

Listener = Callable[[str, Any, int, bool], None]


class MQTTBus:
    """Thin MQTT wrapper that fans out received messages to registered listeners."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host: str = host or os.getenv("MQTT_HOST", "mosquitto")
        self.port: int = port or int(os.getenv("MQTT_PORT", "1883"))
        self._client = mqtt.Client(
            client_id="wfc-test-orchestrator",
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect  # pyright: ignore[reportAttributeAccessIssue]
        self._connected = False
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._all_messages: list[dict[str, Any]] = []
        self._max_transcript: Final[int] = 5000

    # lifecycle

    def start(self) -> None:
        """Connect to the broker in a background daemon thread."""
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                self._client.connect(self.host, self.port)
                self._client.loop_forever()
            except Exception as exc:
                print(f"[MQTTBus] connect failed: {exc} \u2014 retrying in 3s")
                time.sleep(3)

    @property
    def connected(self) -> bool:
        return self._connected

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: dict[str, Any], rc: int, props: Any | None = None
    ) -> None:
        self._connected = rc == 0
        client.subscribe("wfc/#", qos=1)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: int, rc: int, props: Any | None = None) -> None:
        self._connected = False

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            payload: Any = json.loads(msg.payload.decode())
        except Exception:
            payload = msg.payload.decode(errors="replace")

        record: dict[str, Any] = {
            "topic": msg.topic,
            "payload": payload,
            "qos": msg.qos,
            "retain": bool(msg.retain),
            "ts": time.time(),
        }
        with self._lock:
            self._all_messages.append(record)
            if len(self._all_messages) > self._max_transcript:
                self._all_messages.pop(0)
            listeners = list(self._listeners)

        for fn in listeners:
            try:
                fn(msg.topic, payload, msg.qos, bool(msg.retain))
            except Exception as exc:
                print(f"[MQTTBus] listener error: {exc}")

    # public API used by ScenarioRunner

    def subscribe(self, topic_filter: str) -> None:
        pass

    def unsubscribe(self, topic_filter: str) -> None:
        pass

    def publish(self, topic: str, payload: Any, qos: int = 1, retain: bool = False) -> None:
        data = json.dumps(payload) if isinstance(payload, dict) else payload
        self._client.publish(topic, data, qos=qos, retain=retain)

    def register_listener(self, fn: Listener, replay_messages: bool = True) -> Callable[[], None]:
        """Register a listener and return an unregister callable."""
        with self._lock:
            if replay_messages and self._all_messages:
                for msg in self._all_messages:
                    try:
                        fn(msg["topic"], msg["payload"], msg["qos"], msg["retain"])
                    except Exception as exc:
                        print(f"[MQTTBus] replay listener error: {exc}")
            self._listeners.append(fn)

        def _unregister() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return _unregister

    def get_transcript(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._all_messages[-limit:])
