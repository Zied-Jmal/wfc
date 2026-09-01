"""swarm_leader_node.py
SwarmLeaderNode - The Tactical Brain
- Receive commander commands & ACK them (via FieldNode)
- Ingest raw drone telemetry every 2s
- Run Situational Analysis Loop every 2s
- Publish SwarmStatusSnapshot every 10s
- Dispatch drone commands (DISPATCH, RECALL, UPDATE_TASK)
- Publish fire events (intensity, rekindled, verified)
- Run Bully Election if is_backup=True
NEVER: publishes wfc/events/fire detection events (sensor).
NEVER: publishes wfc/state/snapshot (commander only).
NEVER: talks directly to backup-commander.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Final

from core.aggregator.telemetry_aggregator import TelemetryAggregator
from core.election.election_state import ElectionState
from core.election.leader_election import LeaderElection
from core.node.field_node import FieldNode
from core.state.drone_registry import DroneRecord, DroneRegistry
from core.tactics.fire_tactics import DroneAssignment, FireTactics
from core.utils.config import (
    get_election_timeout,
    get_leader_heartbeat_timeout,
    get_swarm_status_interval,
)
from core.utils.logger import log
from wfc_shared.enums.capabilities import (
    HEARTBEAT,
    LEADER_BACKUP,
    RECEIVE_COMMANDS,
    SWARM_LEAD,
    TELEMETRY,
)
from wfc_shared.enums.command_types import (
    ABORT_MISSION,
    CONFIRM_LEADERSHIP,
    CONTAIN_FIRE,
    REASSIGN_LEADER,
    RECALL_DRONE,
    REINFORCE_FIRE,
    RESPOND_TO_FIRE,
    STAND_DOWN,
    UPDATE_TASK,
)
from wfc_shared.enums.node_types import SWARM_LEADER
from wfc_shared.enums.topics import (
    FIRE_INTENSITY,
    FIRE_REKINDLED_TOPIC,
    FIRE_VERIFIED_TOPIC,
    NODES_HEARTBEAT_WILDCARD,
    TELEMETRY_WILDCARD,
    command_topic,
    registry_announce_topic,
    swarm_election_topic,
    swarm_internal_topic,
    swarm_status_topic,
)
from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.telemetry import (
    DroneTelemetry,
    FireIntensityUpdate,
    SwarmStatusSnapshot,
)


class SwarmLeaderNode(FieldNode):
    """
    The Tactical Brain of a drone swarm.

    Args:
        node_id: This leader's unique ID (e.g. "sl-A-01").
        zone: Operational zone label.
        location: GPS (lat, lon).
        backup_peers: node_ids of other backup leaders in this zone.
        is_backup: True when this node starts as backup, monitoring the active leader.
    """

    _LOST_GRACE_PERIOD: Final = 15.0

    def __init__(
        self,
        node_id: str,
        zone: str,
        location: tuple[float, float],
        backup_peers: list[str] | None = None,
        is_backup: bool = False,
    ) -> None:
        caps = [RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY]
        if is_backup:
            caps.append(LEADER_BACKUP)
        else:
            caps.append(SWARM_LEAD)

        super().__init__(
            node_id=node_id,
            node_type=SWARM_LEADER,
            capabilities=caps,
            zone=zone,
            location=location,
        )

        self._is_backup = is_backup
        self._backup_peers = backup_peers or []
        self._current_fire_id: str | None = None
        self._current_fire_pos: tuple[float, float] | None = None

        # Internal components
        self._aggregator = TelemetryAggregator(leader_id=node_id)
        self._tactics = FireTactics()
        self._drone_reg = DroneRegistry()

        # Status publish interval
        self._status_interval = get_swarm_status_interval()

        # Leader heartbeat monitoring (only when backup)
        self._current_leader_id: str | None = None
        self._leader_last_seen: float | None = None
        self._hb_timeout = get_leader_heartbeat_timeout()

        # Election engine (only for backup nodes)
        self._election_state = ElectionState()
        self._election_engine: LeaderElection | None = None
        if is_backup:
            self._election_engine = LeaderElection(
                node_id=node_id,
                zone=zone or "default",
                peer_ids=self._backup_peers,
                state=self._election_state,
                mqtt=self.mqtt,
                on_win=self._on_election_win,
                on_lost=self._on_election_lost,
                timeout=get_election_timeout(),
            )

        # Lost drone detection (stalled telemetry, MQTT still connected)
        self._lost_tracker: dict[str, float] = {}

        # Threads
        self._analysis_thread: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._running = False

    # LIFECYCLE

    def start(self) -> None:
        """
        Start the leader node - subscribe to telemetry, election, heartbeat topics
        and launch analysis, status, and monitor background threads.

        Args:
            None

        Returns:
            None
        """
        super().start()

        # Subscribe to drone telemetry wildcard
        self.mqtt.subscribe(TELEMETRY_WILDCARD, qos=0)
        # Subscribe to election internal messages
        self.mqtt.subscribe(swarm_internal_topic(self.node_id), qos=1)
        # Subscribe to heartbeats (for leader monitoring)
        self.mqtt.subscribe(NODES_HEARTBEAT_WILDCARD, qos=0)

        self._running = True

        # Situational analysis loop (every 2s)
        self._analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self._analysis_thread.start()

        # Status publish loop (every 10s)
        self._status_thread = threading.Thread(target=self._status_publish_loop, daemon=True)
        self._status_thread.start()

        # Leader heartbeat monitor (backup only)
        if self._is_backup:
            self._monitor_thread = threading.Thread(target=self._leader_monitor_loop, daemon=True)
            self._monitor_thread.start()

        log("SwarmLeaderNode", f"{self.node_id} started (backup={self._is_backup})", channel="SYSTEM")

    def stop(self) -> None:
        """Stop the leader node and all background loops."""
        self._running = False
        super().stop()

    # MESSAGE ROUTING

    def handle_message(self, topic: str, payload: dict[str, Any] | str) -> None:
        """
        Route incoming MQTT messages to telemetry, heartbeat, election,
        or command handlers.

        Args:
            topic: MQTT topic.
            payload: Parsed message payload.

        Returns:
            None
        """
        if not isinstance(payload, dict):
            return

        # Drone telemetry
        if topic.startswith("wfc/telemetry/"):
            self._on_telemetry(topic, payload)
            return

        # Heartbeats (for leader monitoring)
        if "heartbeat" in topic:
            node_id = payload.get("node_id")
            if node_id and node_id == self._current_leader_id:
                self._leader_last_seen = time.time()
            return

        # Internal election messages
        if topic == swarm_internal_topic(self.node_id):
            if self._election_engine:
                self._election_engine.on_election_message(payload)  # pyright: ignore[reportUnknownMemberType]
            return

        # Delegate command + registry to FieldNode
        super().handle_message(topic, payload)

    # REGISTRY ANNOUNCE drone / leader discovery

    def _on_registry_announce(self, payload: dict[str, Any]) -> None:
        """Handle registry announces for leader discovery and drone registration."""
        node_id = payload.get("node_id", "")
        caps = payload.get("capabilities", [])
        status = payload.get("status", "ONLINE")
        election_meta = payload.get("election")

        # Leader discovery (spec Part 8.3)
        if SWARM_LEAD in caps and node_id != self.node_id:
            term = election_meta.get("term", 1) if election_meta else 1
            self.set_current_leader(node_id, term)
            return

        # Drone registration / unregistration
        node_type = payload.get("node_type", "")
        if node_type in ("SCOUT_DRONE", "FIREFIGHTING_DRONE"):
            if status == "OFFLINE":
                # Check what task the drone was doing before unregistering
                rec = self._drone_reg.get(node_id)
                role = rec.role if rec else None
                task = rec.last_telemetry.task if (rec and rec.last_telemetry) else None
                self._drone_reg.unregister(node_id)
                self._lost_tracker.pop(node_id, None)
                log("SwarmLeaderNode", f"drone {node_id} went OFFLINE - unregistered", channel="REGISTRY")
                if task in ("SCOUTING", "SUPPRESSING"):
                    log(
                        "SwarmLeaderNode",
                        f"lost drone {node_id} had active task {task} - dispatching replacement",
                        channel="REGISTRY",
                    )
                    self._replace_lost_drone(node_id, role, task)  # pyright: ignore[reportArgumentType]
            elif status == "ONLINE":
                role = "SCOUT" if node_type == "SCOUT_DRONE" else "FIREFIGHTING"
                loc = payload.get("location", (0.0, 0.0))
                self._drone_reg.register(node_id, role, tuple(loc) if loc else (0.0, 0.0))  # pyright: ignore[reportArgumentType]

    # COMMAND EXECUTION (from commander)

    def _execute_command(
        self,
        command_type: str,
        fire_payload: dict[str, Any],
        trace_id: str,
    ) -> None:
        """Dispatch commander-level commands to the appropriate handler."""
        log("SwarmLeaderNode", f"executing {command_type}", channel="COMMANDS")

        if command_type == RESPOND_TO_FIRE:
            self._cmd_respond_to_fire(fire_payload)

        elif command_type == CONTAIN_FIRE:
            self._cmd_contain_fire(fire_payload)

        elif command_type == STAND_DOWN:
            self._cmd_stand_down()

        elif command_type == REINFORCE_FIRE:
            self._cmd_reinforce_fire(fire_payload)

        elif command_type == ABORT_MISSION:
            self._cmd_stand_down()  # abort = immediate recall

        elif command_type == REASSIGN_LEADER:
            # New zone/fire assignment - update internal fire_id and re-announce
            new_fire_id = fire_payload.get("fire_id")
            if new_fire_id:
                self._current_fire_id = new_fire_id
                self._aggregator.set_fire_id(new_fire_id)
            log("SwarmLeaderNode", f"reassigned to fire {new_fire_id}", channel="COMMANDS")

        elif command_type == CONFIRM_LEADERSHIP:
            # Sync current fire state after election win
            fire_id = fire_payload.get("fire_id")
            if fire_id:
                self._current_fire_id = fire_id
                self._aggregator.set_fire_id(fire_id)
            log("SwarmLeaderNode", f"leadership confirmed fire={fire_id}", channel="COMMANDS")

    # Individual command handlers

    def _cmd_respond_to_fire(self, payload: dict[str, Any]) -> None:
        """Handle RESPOND_TO_FIRE - assign scouts and fighters."""
        fire_id = payload.get("fire_id", str(uuid.uuid4())[:8])
        raw = payload.get("location_coords")
        fire_pos = tuple(raw) if raw else (0.0, 0.0)
        severity = payload.get("severity", "HIGH")  # was hardcoded "HIGH"
        self._current_fire_id = fire_id
        self._current_fire_pos = fire_pos
        self._aggregator.set_fire_id(fire_id, severity)

        scouts = self._drone_reg.get_by_role("SCOUT")
        fighters = self._drone_reg.get_by_role("FIREFIGHTING")
        assignments = self._tactics.assign_respond_to_fire(
            fire_id=fire_id, fire_pos=fire_pos, severity=severity, scouts=scouts, fighters=fighters
        )
        self._dispatch_assignments(assignments)

    def _cmd_contain_fire(self, payload: dict[str, Any]) -> None:
        """Handle CONTAIN_FIRE - establish perimeter."""
        fire_id = payload.get("fire_id", self._current_fire_id)
        raw = payload.get("location_coords")
        fire_pos = tuple(raw) if raw else (0.0, 0.0)
        severity = payload.get("severity", "HIGH")
        perimeter_m = payload.get("perimeter_m")

        self._current_fire_id = fire_id
        self._current_fire_pos = fire_pos

        scouts = self._drone_reg.get_by_role("SCOUT")
        fighters = self._drone_reg.get_by_role("FIREFIGHTING")
        assignments = self._tactics.assign_contain_fire(
            fire_id=fire_id,
            fire_pos=fire_pos,
            severity=severity,
            perimeter_m=perimeter_m,
            scouts=scouts,
            fighters=fighters,
        )
        self._dispatch_assignments(assignments)

    def _cmd_stand_down(self) -> None:
        """Handle STAND_DOWN / ABORT_MISSION - recall all drones."""
        all_drones = self._drone_reg.get_all()
        assignments = self._tactics.assign_stand_down(all_drones)
        self._dispatch_assignments(assignments)
        self._current_fire_id = None
        self._current_fire_pos = None
        self._lost_tracker.clear()
        self._aggregator.set_fire_id(None)  # pyright: ignore[reportArgumentType]

    def _cmd_reinforce_fire(self, payload: dict[str, Any]) -> None:
        """Handle REINFORCE_FIRE - deploy additional drones."""
        fire_id = payload.get("fire_id", self._current_fire_id)
        raw = payload.get("location_coords")
        fire_pos = tuple(raw) if raw else (0.0, 0.0)
        new_scout_ids = payload.get("new_scouts", [])
        new_fighter_ids = payload.get("new_fighters", [])

        self._current_fire_pos = fire_pos

        new_scouts = [self._drone_reg.get(d) for d in new_scout_ids if self._drone_reg.get(d)]
        new_fighters = [self._drone_reg.get(d) for d in new_fighter_ids if self._drone_reg.get(d)]

        assignments = self._tactics.assign_reinforce(  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
            fire_id, fire_pos, new_scouts, new_fighters, self._drone_reg.count()
        )
        self._dispatch_assignments(assignments)  # pyright: ignore[reportUnknownArgumentType]

    # DRONE COMMAND DISPATCH

    def _dispatch_assignments(self, assignments: list[DroneAssignment]) -> None:
        """Deduplicate and send drone commands for each assignment."""
        # Deduplicate: keep highest-priority assignment per drone
        best: dict[str, DroneAssignment] = {}
        for a in assignments:
            prev = best.get(a.drone_id)
            if prev is None or a.priority > prev.priority:
                best[a.drone_id] = a
        for a in best.values():
            if a.task == "RETURNING":
                self._send_drone_command(a.drone_id, RECALL_DRONE, {})
            elif a.task in ("SCOUTING", "SUPPRESSING", "IDLE"):
                payload = {"task": a.task}
                if a.target_pos:
                    payload["target_pos"] = list(a.target_pos)  # pyright: ignore[reportArgumentType]
                if a.severity:
                    payload["severity"] = a.severity
                self._send_drone_command(a.drone_id, UPDATE_TASK, payload)

    def _send_drone_command(self, drone_id: str, command_type: str, payload: dict[str, Any]) -> None:
        """Publish a command to a specific drone and log the result."""
        trace_id = str(uuid.uuid4())
        try:
            self.mqtt.publish(
                command_topic(drone_id),
                {
                    "trace_id": trace_id,
                    "target_node": drone_id,  # was missing; required by Command schema
                    "command_type": command_type,
                    "payload": payload,
                    "from": self.node_id,
                    "timestamp": time.time(),
                },
                qos=1,
            )
            log("SwarmLeaderNode", f" {command_type} to {drone_id} trace={trace_id[:8]}", channel="COMMANDS")
        except UnicodeEncodeError:
            log(
                "SwarmLeaderNode",
                f"failed to send {command_type} to {drone_id} (trace={trace_id[:8]}) - payload contains non-UTF8 data",
                channel="COMMANDS",
            )

    # TELEMETRY INGESTION
    def _on_telemetry(self, topic: str, payload: dict[str, Any]) -> None:
        """Ingest and aggregate drone telemetry from MQTT."""
        try:
            telem = DroneTelemetry(**payload)
        except Exception as exc:
            log("SwarmLeaderNode", f"bad telemetry payload: {exc}", channel="SYSTEM")
            return

        # Only ingest telemetry for drones that report to THIS leader
        if telem.leader_id != self.node_id:
            return

        self._aggregator.ingest(telem)
        self._drone_reg.update_telemetry(telem.drone_id, telem)

    # ANALYSIS LOOP (every 2s)

    def _analysis_loop(self) -> None:
        """Background loop: check lost drones, reassign tactics, publish intensity."""
        prev_intensity: str | None = None
        while self._running:
            time.sleep(2)
            try:
                self._check_lost_drones()
                snapshot = self._aggregator.snapshot()
                assignments = self._tactics.reassess(snapshot, self._drone_reg)
                self._dispatch_assignments(assignments)

                # Publish intensity update if changed
                if snapshot.fire_intensity != prev_intensity and self._current_fire_id:
                    self._publish_intensity_update(snapshot)
                    prev_intensity = snapshot.fire_intensity

            except Exception as exc:
                log("SwarmLeaderNode", f"analysis loop error: {exc}", channel="SYSTEM")

    # STATUS PUBLISH LOOP (every 10s)

    def _status_publish_loop(self) -> None:
        """Background loop: publish SwarmStatusSnapshot on a fixed interval."""
        while self._running:
            time.sleep(self._status_interval)
            try:
                snapshot = self._aggregator.snapshot()
                self.mqtt.publish(
                    swarm_status_topic(self.node_id),
                    snapshot.model_dump(),
                    qos=1,  # spec requires QoS 1 for leader→commander snapshots
                )
                log(
                    "SwarmLeaderNode",
                    f"published status snapshot active={snapshot.active_drones} intensity={snapshot.fire_intensity}",
                    channel="SYSTEM",
                )
            except Exception as exc:
                log("SwarmLeaderNode", f"status publish error: {exc}", channel="SYSTEM")

    # LOST DRONE DETECTION & MITIGATION

    def _check_lost_drones(self) -> None:
        """
        Detect hung drones (MQTT connected but telemetry stalled).
        After LOST_GRACE_PERIOD, unregister and dispatch replacement.
        """
        now = time.time()
        lost = self._drone_reg.get_lost()
        lost_ids = {d.drone_id for d in lost}

        # Clear recovered drones from tracker
        for rid in list(self._lost_tracker.keys()):
            if rid not in lost_ids:
                del self._lost_tracker[rid]

        # Track newly lost drones
        for d in lost:
            if d.drone_id not in self._lost_tracker:
                self._lost_tracker[d.drone_id] = now

        # Act on drones lost beyond grace period
        for d in lost:
            lost_since = self._lost_tracker.get(d.drone_id)
            if lost_since is None:
                continue
            if (now - lost_since) < self._LOST_GRACE_PERIOD:
                continue
            task = d.last_telemetry.task if d.last_telemetry else None
            if task in ("SCOUTING", "SUPPRESSING"):
                log(
                    "SwarmLeaderNode",
                    f"drone {d.drone_id} lost {now - lost_since:.0f}s (task={task}) - unregistering + replacing",
                    channel="SYSTEM",
                )
                self._drone_reg.unregister(d.drone_id)
                self._lost_tracker.pop(d.drone_id, None)
                self._replace_lost_drone(d.drone_id, d.role, task)
            else:
                self._drone_reg.unregister(d.drone_id)
                self._lost_tracker.pop(d.drone_id, None)
                log("SwarmLeaderNode", f"drone {d.drone_id} lost (no active task) - unregistered", channel="SYSTEM")

    def _replace_lost_drone(self, lost_id: str, role: str, task: str) -> None:
        """Find an idle replacement drone and dispatch it to the current fire."""
        if not self._current_fire_pos:
            log("SwarmLeaderNode", f"cannot replace {lost_id}: no current fire position known", channel="SYSTEM")
            return
        # Search for an idle drone of the same role with sufficient battery & payload
        replacement: DroneRecord | None = None
        for d in self._drone_reg.get_idle():
            if d.role != role:
                continue
            t = d.last_telemetry
            if t and t.battery_pct > 0.25:
                payload_ok = t.payload_litres is None or t.payload_litres > 1.5  # pyright: ignore[reportUnnecessaryComparison]
                if payload_ok:
                    replacement = d
                    break
        if replacement:
            self._send_drone_command(
                replacement.drone_id, UPDATE_TASK, {"task": task, "target_pos": list(self._current_fire_pos)}
            )
            log(
                "SwarmLeaderNode",
                f"dispatched {replacement.drone_id} as replacement for lost {lost_id} (task={task})",
                channel="SYSTEM",
            )
        else:
            log("SwarmLeaderNode", f"no idle {role} available to replace lost {lost_id}", channel="SYSTEM")

    # FIRE EVENT PUBLISHING

    def _publish_intensity_update(self, snapshot: SwarmStatusSnapshot) -> None:
        """Publish a FireIntensityUpdate when intensity changes."""
        update = FireIntensityUpdate(
            fire_id=self._current_fire_id,  # pyright: ignore[reportArgumentType]
            leader_id=self.node_id,
            timestamp=time.time(),
            new_intensity=snapshot.fire_intensity,
            perimeter_m=snapshot.perimeter_estimate_m,
            spread_rate=snapshot.spread_rate,
        )
        self.mqtt.publish(FIRE_INTENSITY, update.model_dump(), qos=1)

    def publish_fire_rekindled(self, fire_id: str, location: tuple[float, float]) -> None:
        """Publish a rekindled fire event to the commander."""
        self.mqtt.publish(
            FIRE_REKINDLED_TOPIC,
            {
                "fire_id": fire_id,
                "leader_id": self.node_id,
                "timestamp": time.time(),
                "location": list(location),
            },
            qos=1,
        )

    def publish_fire_verified(self, fire_id: str, location: tuple[float, float], intensity: str) -> None:
        """Publish a verified fire event to the commander."""
        self.mqtt.publish(
            FIRE_VERIFIED_TOPIC,
            {
                "fire_id": fire_id,
                "leader_id": self.node_id,
                "timestamp": time.time(),
                "location": list(location),
                "intensity": intensity,
            },
            qos=1,
        )

    # LEADER HEARTBEAT MONITOR (backup only)

    def set_current_leader(self, leader_id: str, term: int = 1) -> None:
        """Called when a SWARM_LEAD announce is seen - update leader and term."""
        self._current_leader_id = leader_id
        self._leader_last_seen = time.time()
        self._election_state.accept_term(term)
        log("SwarmLeaderNode", f"current leader set to {leader_id} (term={term})", channel="SYSTEM")

    def _leader_monitor_loop(self) -> None:
        """Backup-only: detect leader heartbeat timeout and start election."""
        while self._running:
            time.sleep(1)
            if self._current_leader_id is None:
                continue
            if self._leader_last_seen is None:
                continue
            elapsed = time.time() - self._leader_last_seen
            if elapsed > self._hb_timeout and not self._election_state.in_election:
                log(
                    "SwarmLeaderNode",
                    f"leader {self._current_leader_id} timed out after {elapsed:.1f}s - starting election",
                    channel="SYSTEM",
                )
                self._current_leader_id = None
                if self._election_engine:
                    self._election_engine.start_election()

    # ELECTION CALLBACKS

    def _on_election_win(self) -> None:
        """Called by LeaderElection when this node wins - promote to SWARM_LEAD."""
        term = self._election_state.term
        prev_lead = self._current_leader_id or "unknown"

        log("SwarmLeaderNode", f"WON election term={term} - promoting to SWARM_LEAD", channel="SYSTEM")

        # Update capabilities: remove LEADER_BACKUP, add SWARM_LEAD
        if LEADER_BACKUP in self.capabilities:
            self.capabilities.remove(LEADER_BACKUP)
        if SWARM_LEAD not in self.capabilities:
            self.capabilities.append(SWARM_LEAD)
        self._is_backup = False

        election_meta = {
            "type": "BULLY",
            "fire_id": self._current_fire_id,
            "previous_leader": prev_lead,
            "elected_at": time.time(),
            "term": term,
            "swarm_size": self._drone_reg.count(),
            "active_drones": len(self._drone_reg.get_active()),
        }

        # Action 1 - Re-announce with SWARM_LEAD + election metadata
        announcement = NodeAnnouncement(
            node_id=self.node_id,
            node_type=self.node_type,  # pyright: ignore[reportArgumentType]
            capabilities=self.capabilities,  # pyright: ignore[reportArgumentType]
            status="ONLINE",
            zone=self.zone,
            location=self.location,
            election=election_meta,
        )
        self.mqtt.publish_retained(
            registry_announce_topic(self.node_id),
            announcement.model_dump(),
            qos=1,
        )

        # Action 2 - Publish SWARM_LEADER_ELECTED event
        self.mqtt.publish(
            swarm_election_topic(self.zone or "default"),
            {
                "fire_id": self._current_fire_id,
                "new_leader_id": self.node_id,
                "old_leader_id": prev_lead,
                "election_type": "BULLY",
                "term": term,
                "swarm_size": self._drone_reg.count(),
                "timestamp": time.time(),
            },
            qos=1,
        )

        # Resume fire operations immediately - no CONFIRM_LEADERSHIP wait
        log("SwarmLeaderNode", "election win - resuming fire operations immediately", channel="SYSTEM")

    def _on_election_lost(self) -> None:
        """Called by LeaderElection when another node wins - remain backup."""
        log("SwarmLeaderNode", "election lost - remaining backup", channel="SYSTEM")
