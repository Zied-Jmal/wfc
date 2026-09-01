from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Final

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from dashboard.state import swarm_state
from wfc_shared.enums.events import (
    FIRE_INTENSITY_UPDATE,
    FIRE_REKINDLED,
    FIRE_VERIFIED,
)
from wfc_shared.enums.topics import (
    ACK,
    APPROVAL_PENDING,
    EVENTS_FIRE,  # "wfc/events/fire"           - sensor trigger
    FIRE_INTENSITY,  # "wfc/events/fire/intensity"  - leader trigger
    FIRE_REKINDLED_TOPIC,
    FIRE_VERIFIED_TOPIC,
    STATE_SNAPSHOT,
    SWARM_ELECTION_PREFIX,
    SWARM_STATUS_PREFIX,
    SYSTEM_FAILOVER,
    WFC_ALL,
)
from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.events import FireEvent
from wfc_shared.schemas.telemetry import DroneTelemetry, FireIntensityUpdate, SwarmStatusSnapshot

MQTT_HOST: Final[str] = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT: Final[int] = int(os.getenv("MQTT_PORT", "1883"))

_TELEMETRY_PREFIX: Final[str] = "wfc/telemetry/"


class MQTTBridge:
    def __init__(self) -> None:
        self._client = mqtt.Client(
            client_id="wfc-ctrl-dash-bridge",
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect  # pyright: ignore[reportAttributeAccessIssue]
        self._connected = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    @property
    def connected(self) -> bool:
        return self._connected

    def _run(self) -> None:
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        while True:
            try:
                self._client.connect(MQTT_HOST, MQTT_PORT)
                self._client.loop_forever()
            except Exception as exc:
                print(f"[MQTTBridge] {exc} - retrying in 3s")
                time.sleep(3)

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: dict[str, int], rc: int, props: Any = None
    ) -> None:
        self._connected = True
        client.subscribe(WFC_ALL, qos=0)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: int, rc: Any, props: Any = None) -> None:
        self._connected = False

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            raw = json.loads(msg.payload.decode())
        except Exception:
            return
        if not isinstance(raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            return
        raw: dict[str, Any]

        # Registry announces
        if topic.startswith("wfc/registry/announce/"):
            try:
                ann = NodeAnnouncement.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad NodeAnnouncement: {exc}")
                return
            swarm_state.apply_announcement(ann)
            if ann.status == "OFFLINE":
                swarm_state.mark_offline(ann.node_id)
            swarm_state.add_event({"type": "ANNOUNCE", "node_id": ann.node_id, "status": ann.status})
            return

        # Drone telemetry (V2: ISO fields, GPS position)
        if topic.startswith(_TELEMETRY_PREFIX):
            try:
                t = DroneTelemetry.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad DroneTelemetry on {topic}: {exc}")
                return
            swarm_state.apply_telemetry(t)
            return

        # Leader swarm status (V2: avg_payload_litres etc.)
        if topic.startswith(SWARM_STATUS_PREFIX):
            leader_id = topic[len(SWARM_STATUS_PREFIX) :]
            try:
                snap = SwarmStatusSnapshot.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad SwarmStatusSnapshot: {exc}")
                return
            swarm_state.apply_swarm_status(leader_id, snap)
            if snap.fire_id:
                swarm_state.upsert_fire_from_snapshot(snap.fire_id, leader_id, snap.fire_intensity)
            return

        # Elections
        if topic.startswith(SWARM_ELECTION_PREFIX):
            swarm_state.add_election_event(raw)  # pyright: ignore[reportArgumentType]
            swarm_state.add_event(
                {
                    "type": "ELECTION",
                    "winner": raw.get("new_leader_id"),
                    "old_leader": raw.get("old_leader_id"),
                    "term": raw.get("term"),
                }
            )
            return

        # Sub-paths before bare EVENTS_FIRE check

        # Fire verified
        if topic == FIRE_VERIFIED_TOPIC:
            try:
                upd = FireIntensityUpdate.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad fire/verified: {exc}")
                return
            swarm_state.apply_intensity_update(upd)
            swarm_state.add_event(
                {
                    "type": FIRE_VERIFIED,
                    "fire_id": upd.fire_id,
                    "leader_id": upd.leader_id,
                    "intensity": upd.new_intensity,
                }
            )
            return

        # Fire rekindled
        if topic == FIRE_REKINDLED_TOPIC:
            try:
                upd = FireIntensityUpdate.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad fire/rekindled: {exc}")
                return
            swarm_state.apply_intensity_update(upd)
            swarm_state.add_event(
                {
                    "type": FIRE_REKINDLED,
                    "fire_id": upd.fire_id,
                    "leader_id": upd.leader_id,
                    "intensity": upd.new_intensity,
                }
            )
            return

        # Fire intensity update (V2: includes wind_speed_mps)
        if topic == FIRE_INTENSITY:
            try:
                upd = FireIntensityUpdate.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad FireIntensityUpdate: {exc}")
                return
            swarm_state.apply_intensity_update(upd)
            swarm_state.add_event(
                {
                    "type": FIRE_INTENSITY_UPDATE,
                    "fire_id": upd.fire_id,
                    "leader_id": upd.leader_id,
                    "intensity": upd.new_intensity,
                    "wind_mps": upd.wind_speed_mps,
                }
            )
            return

        # Fire lifecycle (FIRE_DETECTED | FIRE_SUPPRESSED | FIRE_CONTAINED)
        # Exact match on bare EVENTS_FIRE - sub-paths already caught above
        if topic == EVENTS_FIRE:
            try:
                ev = FireEvent.model_validate(raw)
            except Exception as exc:
                print(f"[MQTTBridge] bad FireEvent: {exc}")
                return
            swarm_state.apply_fire_event(ev)
            swarm_state.add_event(
                {
                    "type": ev.event_type,
                    "fire_id": ev.payload.fire_id,
                    "source": ev.source,
                    "severity": ev.payload.severity,
                    "zone": ev.payload.zone,
                }
            )
            return

        # Commander state snapshot
        if topic == STATE_SNAPSHOT:
            node_id = raw.get("node_id") or raw.get("commander_id")
            if node_id:
                swarm_state.apply_commander_snapshot(
                    node_id=node_id,
                    is_active=raw.get("is_active", True),
                    term=raw.get("term"),
                )
            swarm_state.add_event({"type": "STATE_SNAPSHOT", "node_id": node_id})
            return

        # Commander failover
        if topic == SYSTEM_FAILOVER:
            swarm_state.add_event(
                {
                    "type": "FAILOVER",
                    "new_owner": raw.get("new_owner"),
                    "old_owner": raw.get("old_owner"),
                    "term": raw.get("term"),
                }
            )
            return

        # Approval pending
        if topic == APPROVAL_PENDING:
            swarm_state.add_pending_approval(raw)  # pyright: ignore[reportArgumentType]
            swarm_state.add_event(
                {
                    "type": "APPROVAL_PENDING",
                    "command": raw.get("command", {}).get("command_type"),
                    "request_id": (raw.get("pending_id") or "")[:8],
                }
            )
            return

        # ACKs
        if topic == ACK:
            swarm_state.add_event(
                {
                    "type": "ACK",
                    "node_id": raw.get("node_id"),
                    "status": raw.get("status"),
                    "trace_id": (raw.get("trace_id") or "")[:8],
                }
            )
            return

        # Heartbeats
        if "heartbeat" in topic:
            node_id = raw.get("node_id")
            if node_id:
                node_type = raw.get("type", "UNKNOWN")
                swarm_state.update_heartbeat(node_id, node_type)
            return


bridge: Final[MQTTBridge] = MQTTBridge()
