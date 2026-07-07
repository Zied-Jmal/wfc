#!/usr/bin/env python3
"""
Manual E2E Test — injects a real fire and watches the full pipeline.
"""
import json
import subprocess
import threading
import time

import requests

DASHBOARD_URL = "http://localhost:8080"

captured_mqtt = []
mqtt_done = threading.Event()


def mqtt_capture():
    try:
        result = subprocess.run(
            [
                "docker", "exec", "wfc-mosquitto",
                "mosquitto_sub",
                "-t", "wfc/events/#",
                "-t", "wfc/approval/#",
                "-t", "wfc/command/#",
                "-t", "wfc/ack",
                "-t", "wfc/system/#",
                "-t", "wfc/state/#",
                "-t", "wfc/swarm/#",
                "-C", "60", "-W", "35", "-v",
            ],
            capture_output=True, text=True, timeout=40,
        )
        captured_mqtt.append(result.stdout)
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        captured_mqtt.append(f"ERROR: {e}")
    finally:
        mqtt_done.set()


def check_api(path, label):
    try:
        r = requests.get(f"{DASHBOARD_URL}{path}", timeout=5)
        print(f"[{label}] HTTP {r.status_code}")
        return r.json()
    except Exception as e:
        print(f"[{label}] Error: {e}")
        return None


# Start MQTT subscriber
print("=" * 70)
print("MANUAL E2E TEST — REAL FIRE SCENARIO")
print("=" * 70)
print()

t = threading.Thread(target=mqtt_capture, daemon=True)
t.start()
time.sleep(2)

# 1. Initial state check
print("--- BEFORE FIRE ---")
nodes = check_api("/api/nodes", "Nodes")
if nodes:
    for n in nodes:
        nid = n.get("node_id", "?")
        ntype = n.get("node_type", "?")
        status = n.get("status", "?")
        zone = n.get("zone", "?")
        print(f"  Node: {nid:25s} type={ntype:20s} status={status:10s} zone={zone}")

fires = check_api("/api/fires", "Fires")
if fires:
    print(f"  Fires: {fires}")
approvals = check_api("/api/approvals", "Approvals")
if approvals:
    print(f"  Approvals: {approvals}")
print()

# 2. Inject HIGH-severity fire via Dashboard API
print("--- INJECTING FIRE (HIGH severity) ---")
fire_payload = {
    "event_type": "FIRE_DETECTED",
    "source": "sensor-zone-alpha-01",
    "payload": {
        "fire_id": "demo-fire-001",
        "zone": "zone_alpha",
        "severity": "HIGH",
        "sensor_id": "sensor-zone-alpha-01",
        "location_coords": [36.8065, 10.1815],
    },
}
try:
    r = requests.post(
        f"{DASHBOARD_URL}/api/inject/fire/sensor",
        json=fire_payload,
        timeout=5,
    )
    print(f"  Inject response: {r.status_code} {r.text[:200]}")
except Exception as e:
    print(f"  Inject error: {e}")
print()

# 3. Wait for commander to process
print("--- WAITING FOR PROCESSING (8s) ---")
for i in range(8):
    time.sleep(1)
    print(f"  t+{i+1}s")

print()

# 4. Check state after injection
print("--- AFTER FIRE INJECTION ---")
fires = check_api("/api/fires", "Fires")
if fires:
    for f in (fires if isinstance(fires, list) else [fires]):
        fid = f.get("fire_id", "?")
        sev = f.get("severity", "?")
        state = f.get("state", "?")
        zone = f.get("zone", "?")
        loc = f.get("location_coords", f.get("location", "?"))
        print(f"  Fire: id={fid} severity={sev} state={state} zone={zone} loc={loc}")

approvals = check_api("/api/approvals", "Approvals")
if approvals:
    for a in (approvals if isinstance(approvals, list) else [approvals]):
        pid = a.get("pending_id", a.get("request_id", "?"))
        cmd = a.get("command_type", a.get("command", {}).get("command_type", "?"))
        target = a.get("target_node", "?")
        decision = a.get("decision", a.get("event", "?"))
        print(f"  Approval: pending={str(pid)[:12]} cmd={cmd} target={target} decision={decision}")
print()

# 5. Approve the pending command via the Dashboard
print("--- APPROVING PENDING COMMAND ---")
if approvals:
    if isinstance(approvals, list) and len(approvals) > 0:
        pending_id = approvals[0].get("pending_id", approvals[0].get("request_id"))
        if pending_id:
            approve_payload = {
                "request_id": pending_id,
                "approved": True,
                "reason": "operator-approval-demo",
            }
            try:
                r = requests.post(
                    f"{DASHBOARD_URL}/api/approval/respond",
                    json=approve_payload,
                    timeout=5,
                )
                print(f"  Approve response: {r.status_code} {r.text[:200]}")
            except Exception as e:
                print(f"  Approve error: {e}")
print()

# 6. Wait for dispatch
print("--- WAITING FOR DISPATCH (5s) ---")
time.sleep(5)

# 7. Check final state
print("--- FINAL STATE ---")
fires = check_api("/api/fires", "Fires")
if fires:
    for f in (fires if isinstance(fires, list) else [fires]):
        fid = f.get("fire_id", "?")
        sev = f.get("severity", "?")
        state = f.get("state", "?")
        print(f"  Fire: id={fid} severity={sev} state={state}")

approvals = check_api("/api/approvals", "Approvals")
if approvals:
    for a in (approvals if isinstance(approvals, list) else [approvals]):
        pid = a.get("pending_id", a.get("request_id", "?"))
        cmd = a.get("command_type", a.get("command", {}).get("command_type", "?"))
        decision = a.get("decision", a.get("event", "?"))
        print(f"  Approval: pending={str(pid)[:12]} cmd={cmd} decision={decision}")
print()

# 8. Print MQTT capture
print("=" * 70)
print("MQTT CAPTURE")
print("=" * 70)
mqtt_done.wait(timeout=10)
if captured_mqtt:
    lines = captured_mqtt[0].strip().split("\n")
    for line in lines:
        if line.strip():
            # Split topic and payload
            idx = line.index(" ") if " " in line else -1
            if idx > 0:
                topic = line[:idx]
                payload_preview = line[idx + 1 :][:200]
            else:
                topic = line
                payload_preview = ""
            print(f"\n  TOPIC: {topic}")
            if payload_preview:
                try:
                    pretty = json.dumps(json.loads(payload_preview), indent=4)
                    for pl_line in pretty.split("\n")[:15]:
                        print(f"    {pl_line}")
                except json.JSONDecodeError:
                    print(f"    {payload_preview[:200]}")
else:
    print("  (no MQTT messages captured)")
print()

print("=" * 70)
print("TEST COMPLETE")
print("=" * 70)
