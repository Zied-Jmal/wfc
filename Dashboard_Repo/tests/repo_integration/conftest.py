from __future__ import annotations

import os
import json
import subprocess
import tempfile
import time
import threading
import socket
from typing import Any, Final, Generator

import pytest
import paho.mqtt.client as mqtt

MOSQUITTO_IMAGE: Final[str] = "eclipse-mosquitto:2.0"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "repo_integration: tests requiring a real MQTT broker via Docker")


@pytest.fixture(scope="session")
def mosquitto_broker() -> Generator[tuple[str, int], None, None]:
    name = "wfc-ri-dash-mqtt"
    port = _free_port()
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    conf = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
    conf.write("listener 1883\nallow_anonymous true\nlistener 9001\nprotocol websockets\n")
    conf.close()
    conf_path = conf.name.replace("\\", "/")
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"{port}:1883",
                    "-v", f"{conf_path}:/mosquitto/config/mosquitto.conf:ro",
                    MOSQUITTO_IMAGE], check=True, capture_output=True)
    host = "127.0.0.1"
    for _ in range(30):
        try:
            c = mqtt.Client()
            c.connect(host, port)
            c.disconnect()
            break
        except Exception:
            time.sleep(1)
    else:
        subprocess.run(["docker", "logs", name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        os.unlink(conf.name)
        raise RuntimeError("Mosquitto did not start")
    yield host, port
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    os.unlink(conf.name)


@pytest.fixture
def mqtt_client(mosquitto_broker: tuple[str, int]) -> Generator[tuple[mqtt.Client, list[tuple[str, Any]], threading.Event], None, None]:
    host, port = mosquitto_broker
    received: list[tuple[str, Any]] = []
    ev = threading.Event()

    def on_msg(c: mqtt.Client, u: Any, m: mqtt.MQTTMessage) -> None:
        received.append((m.topic, json.loads(m.payload)))
        ev.set()

    c = mqtt.Client()
    c.on_message = on_msg
    c.connect(host, port)
    c.loop_start()
    yield c, received, ev
    c.loop_stop()
    c.disconnect()


@pytest.fixture
def env_setup(mosquitto_broker: tuple[str, int], tmp_path: Any) -> Generator[None, None, None]:
    host, port = mosquitto_broker
    old = {k: os.environ[k] for k in ("MQTT_HOST", "MQTT_PORT", "WFC_DB_PATH", "DEBUG") if k in os.environ}
    os.environ["MQTT_HOST"] = host
    os.environ["MQTT_PORT"] = str(port)
    os.environ["WFC_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["DEBUG"] = "0"
    yield
    for k in ("MQTT_HOST", "MQTT_PORT", "WFC_DB_PATH", "DEBUG"):
        os.environ.pop(k, None)
    os.environ.update(old)
