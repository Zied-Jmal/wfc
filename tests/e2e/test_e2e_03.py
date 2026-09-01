from __future__ import annotations

import os
import subprocess
import sys
import time
from threading import Event
from typing import Any

import paho.mqtt.client as mqtt


class TestLeaderElectionFailover:
    def test_backup_takes_over_after_active_death(
        self,
        mosquitto_broker: tuple[str, int],
        mqtt_client: tuple[mqtt.Client, list[tuple[str, Any]], Event],
    ) -> None:
        host, port = mosquitto_broker
        sub, all_msgs, msg_ev = mqtt_client
        root: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        swarm: str = os.path.join(root, "Swarm_Repo")
        shared: str = os.path.join(root, "wfc_shared")
        shared_path: str = f"{swarm};{shared}"

        active_id: str = "sl-e2e-active"
        backup_id: str = "sl-e2e-backup"

        def _start_leader(node_id: str, is_backup: bool, backup_peers: str = "") -> subprocess.Popen[bytes]:
            env: dict[str, str] = {
                **os.environ,
                "MQTT_HOST": host,
                "MQTT_PORT": str(port),
                "NODE_ID": node_id,
                "NODE_ZONE": "zone_alpha",
                "NODE_LOCATION": "36.8065,10.1815",
                "IS_BACKUP": str(is_backup).lower(),
                "BACKUP_PEERS": backup_peers,
                "LEADER_HEARTBEAT_TIMEOUT": "6",
                "DEBUG": "0",
                "PYTHONPATH": shared_path,
            }
            return subprocess.Popen(
                [sys.executable, "main_leader.py"],
                cwd=swarm,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        sub.subscribe(f"wfc/registry/announce/{active_id}", qos=1)
        sub.subscribe(f"wfc/registry/announce/{backup_id}", qos=1)
        sub.subscribe("wfc/swarm/election/+", qos=1)
        time.sleep(0.5)

        active: subprocess.Popen[bytes] = _start_leader(active_id, False)
        deadline: float = time.time() + 15
        while time.time() < deadline:
            if any(t == f"wfc/registry/announce/{active_id}" and p.get("status") == "ONLINE" for t, p in all_msgs):
                break
            msg_ev.wait(timeout=1)
            msg_ev.clear()
        else:
            active.kill()
            raise AssertionError(f"Active leader {active_id} did not come online")

        backup: subprocess.Popen[bytes] = _start_leader(backup_id, True, active_id)

        deadline = time.time() + 15
        while time.time() < deadline:
            if any(t == f"wfc/registry/announce/{backup_id}" and p.get("status") == "ONLINE" for t, p in all_msgs):
                break
            msg_ev.wait(timeout=1)
            msg_ev.clear()
        else:
            backup.kill()
            active.kill()
            raise AssertionError(f"Backup leader {backup_id} did not come online")

        time.sleep(6)

        active.terminate()
        try:
            active.wait(3)
        except subprocess.TimeoutExpired:
            active.kill()

        deadline = time.time() + 20
        while time.time() < deadline:
            if any(
                t == f"wfc/registry/announce/{backup_id}"
                and p.get("status") == "ONLINE"
                and "SWARM_LEAD" in p.get("capabilities", [])
                for t, p in all_msgs
            ):
                break
            msg_ev.wait(timeout=1)
            msg_ev.clear()
        else:
            backup.terminate()
            try:
                backup.wait(3)
            except subprocess.TimeoutExpired:
                backup.kill()
            election_msgs: list[tuple[str, Any]] = [(t, p) for t, p in all_msgs if "election" in t]
            raise AssertionError(
                f"Backup {backup_id} did not re-announce as SWARM_LEAD. "
                f"Election events: {election_msgs}. "
                f"Topics after kill: {[t for t, _ in all_msgs]}"
            )

        backup.terminate()
        try:
            backup.wait(5)
        except subprocess.TimeoutExpired:
            backup.kill()
