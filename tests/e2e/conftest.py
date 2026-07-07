from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Generator

import paho.mqtt.client as mqtt
import pytest
from _pytest.config import Config
from _pytest.fixtures import SubRequest

MOSQUITTO_IMAGE: str = "eclipse-mosquitto:2.0"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_announce(host: str, port: int, node_id: str, timeout: int = 20) -> None:
    c = mqtt.Client()
    ev: threading.Event = threading.Event()

    def on_msg(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            if json.loads(msg.payload).get("status") == "ONLINE":
                ev.set()
        except Exception:
            pass

    c.on_message = on_msg
    c.connect(host, port)
    c.subscribe(f"wfc/registry/announce/{node_id}", qos=1)
    c.loop_start()
    ok: bool = ev.wait(timeout=timeout)
    c.loop_stop()
    c.disconnect()
    if not ok:
        raise TimeoutError(f"{node_id} not ONLINE within {timeout}s")


def _wait_http(host: str, port: int, path: str = "/api/nodes", timeout: int = 15) -> None:
    import requests as req
    deadline: float = time.time() + timeout
    while time.time() < deadline:
        try:
            r = req.get(f"http://{host}:{port}{path}", timeout=2)
            if r.status_code in (200, 404):
                return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"HTTP endpoint {host}:{port}{path} not ready in {timeout}s")


def _start_process(
    cwd: str, cmd_args: list[str], env_updates: dict[str, str],
    host: str, port: int, node_id: str,
) -> subprocess.Popen[bytes]:
    env: dict[str, str] = {**os.environ, "MQTT_HOST": host, "MQTT_PORT": str(port), **env_updates}
    proc = subprocess.Popen(cmd_args, cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_announce(host, port, node_id)
    except Exception:
        proc.kill()
        proc.wait(3)
        raise
    return proc


def pytest_configure(config: Config) -> None:
    config.addinivalue_line("markers", "e2e: full end-to-end scenarios with 3+ repo nodes")


@pytest.fixture(scope="session")
def mosquitto_broker() -> Generator[tuple[str, int], None, None]:
    name: str = "wfc-e2e-test-mqtt"
    port: int = _free_port()
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    conf = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
    conf.write("listener 1883\nallow_anonymous true\nlistener 9001\nprotocol websockets\n")
    conf.close()
    conf_path: str = conf.name.replace("\\", "/")
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"{port}:1883",
                    "-v", f"{conf_path}:/mosquitto/config/mosquitto.conf:ro",
                    MOSQUITTO_IMAGE], check=True, capture_output=True)
    host: str = "127.0.0.1"
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
def mqtt_client(
    mosquitto_broker: tuple[str, int],
) -> Generator[tuple[mqtt.Client, list[tuple[str, Any]], threading.Event], None, None]:
    host, port = mosquitto_broker
    received: list[tuple[str, Any]] = []
    ev: threading.Event = threading.Event()

    def on_msg(c: mqtt.Client, u: Any, m: mqtt.MQTTMessage) -> None:
        try:
            received.append((m.topic, json.loads(m.payload)))
        except Exception:
            received.append((m.topic, m.payload))
        ev.set()

    c = mqtt.Client()
    c.on_message = on_msg
    c.connect(host, port)
    c.loop_start()
    yield c, received, ev
    c.loop_stop()
    c.disconnect()


@pytest.fixture
def central_process(
    mosquitto_broker: tuple[str, int],
) -> Generator[subprocess.Popen[bytes], None, None]:
    host, port = mosquitto_broker
    root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    repo: str = os.path.join(root, "Commander_Repo")
    shared: str = os.path.join(root, "wfc_shared")
    db: str = os.path.join(tempfile.gettempdir(), f"e2e_cmd_{uuid.uuid4().hex[:8]}.db")
    proc = _start_process(repo,
        [sys.executable, "-m", "command_nodes.central.main"],
        {"WFC_DB_PATH": db, "DEBUG": "0", "PYTHONPATH": f"{repo};{shared}"},
        host, port, "central-commander")
    yield proc
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        os.unlink(db)
    except OSError:
        pass


@pytest.fixture
def swarm_leader_process(
    mosquitto_broker: tuple[str, int],
) -> Generator[tuple[subprocess.Popen[bytes], str], None, None]:
    host, port = mosquitto_broker
    root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    repo: str = os.path.join(root, "Swarm_Repo")
    shared: str = os.path.join(root, "wfc_shared")
    node_id: str = f"sl-e2e-{uuid.uuid4().hex[:6]}"
    proc = _start_process(repo,
        [sys.executable, "main_leader.py"],
        {"NODE_ID": node_id, "NODE_ZONE": "zone_alpha",
         "NODE_LOCATION": "36.8065,10.1815", "DEBUG": "0",
         "IS_BACKUP": "false",
         "PYTHONPATH": f"{repo};{shared}"},
        host, port, node_id)
    yield proc, node_id
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def backup_leader_process(
    mosquitto_broker: tuple[str, int], request: SubRequest,
) -> Generator[tuple[subprocess.Popen[bytes], str], None, None]:
    host, port = mosquitto_broker
    root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    repo: str = os.path.join(root, "Swarm_Repo")
    shared: str = os.path.join(root, "wfc_shared")
    active_id: str = getattr(request, "param", "sl-e2e-active")
    node_id: str = f"sl-e2e-bu-{uuid.uuid4().hex[:4]}"
    proc = _start_process(repo,
        [sys.executable, "main_leader.py"],
        {"NODE_ID": node_id, "NODE_ZONE": "zone_alpha",
         "NODE_LOCATION": "36.8085,10.1835", "DEBUG": "0",
         "IS_BACKUP": "true", "BACKUP_PEERS": active_id,
         "LEADER_HEARTBEAT_TIMEOUT": "5",
         "PYTHONPATH": f"{repo};{shared}"},
        host, port, node_id)
    yield proc, node_id
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def scout_drone_process(
    mosquitto_broker: tuple[str, int],
) -> Generator[tuple[subprocess.Popen[bytes], str], None, None]:
    host, port = mosquitto_broker
    root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    repo: str = os.path.join(root, "Swarm_Repo")
    shared: str = os.path.join(root, "wfc_shared")
    node_id: str = f"sd-e2e-{uuid.uuid4().hex[:6]}"
    leader_id: str = "unknown"
    proc = _start_process(repo,
        [sys.executable, "main_scout.py"],
        {"NODE_ID": node_id, "NODE_ZONE": "zone_alpha",
         "NODE_LOCATION": "36.8070,10.1825", "LEADER_ID": leader_id,
         "DEBUG": "0", "PYTHONPATH": f"{repo};{shared}"},
        host, port, node_id)
    yield proc, node_id
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def dashboard_process(
    mosquitto_broker: tuple[str, int],
) -> Generator[tuple[subprocess.Popen[bytes], int], None, None]:
    host, port = mosquitto_broker
    root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    repo: str = os.path.join(root, "Dashboard_Repo")
    shared: str = os.path.join(root, "wfc_shared")
    dash_port: int = _free_port()
    map_port: int = _free_port()
    env: dict[str, str] = {**os.environ, "MQTT_HOST": host, "MQTT_PORT": str(port),
                           "DASHBOARD_PORT": str(dash_port), "MAP_PORT": str(map_port),
                           "DEBUG": "0", "PYTHONPATH": f"{repo};{shared}"}
    proc = subprocess.Popen([sys.executable, "main.py"],
                            cwd=repo, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_http("127.0.0.1", dash_port)
    except Exception:
        proc.kill()
        proc.wait(3)
        raise
    yield proc, dash_port
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
