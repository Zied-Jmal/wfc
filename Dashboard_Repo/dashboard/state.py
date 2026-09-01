from __future__ import annotations

import threading
import time
from typing import Any, Final

from wfc_shared.enums.events import FIRE_CONTAINED, FIRE_SUPPRESSED
from wfc_shared.enums.fire_status import ACTIVE as FIRE_ACTIVE
from wfc_shared.enums.fire_status import CONTAINED, SUPPRESSED
from wfc_shared.enums.node_status import ACTIVE, OFFLINE, REGISTERED
from wfc_shared.schemas.announcements import NodeAnnouncement
from wfc_shared.schemas.events import FireEvent
from wfc_shared.schemas.telemetry import DroneTelemetry, FireIntensityUpdate, SwarmStatusSnapshot


class NodeState:
    """Mutable dashboard view of a single node.

    Fields are populated from NodeAnnouncements, DroneTelemetry updates,
    SwarmStatusSnapshots, and Commander snapshots.  See ``__slots__``
    inline comments for per-field ISO units.
    """

    __slots__ = (
        # SwarmStatusSnapshot fields (leader nodes)
        "active_drones",
        "altitude_m_amsl",  # m AMSL
        "announced_at",
        "avg_battery_pct",  # 0.0-1.0
        "avg_payload_litres",  # L
        "battery_pct",  # 0.0-1.0
        "battery_wh",  # Wh
        "capabilities",
        "commander_term",
        "connectivity",
        "distance_to_flame_m",  # m
        "drop_passes",  # int
        "fire_id",
        "fire_intensity",
        "flame_height_m",  # m
        # Commander snapshot
        "is_active_commander",
        "last_seen",
        # Firefighter metrics
        "litres_delivered",  # L
        "location",  # (lat_deg, lon_deg) WGS-84 - updated from telemetry
        "lost_drones",
        "min_battery_wh",  # Wh
        # Identity
        "node_id",
        "node_type",
        "payload_kg",  # kg
        "payload_litres",  # L  (was payload_remaining 0-1)
        "perimeter_estimate",
        "perimeter_estimate_m",  # m
        "pump_active",  # bool
        "smoke_density_mg_m3",  # mg/m³  (was smoke_density)
        "smoke_optical_density",  # 0.0-1.0
        "spread_rate",
        "status",
        "suppression_effectiveness_pct",  # 0.0-1.0
        "suppression_pct",
        "swarm_status",
        # DroneTelemetry V2 - ISO fields
        "task",
        "thermal_coverage_pct",  # 0.0-1.0
        # Scout sensors - ISO units
        "thermal_peak_temp_c",  # °C
        "total_litres_delivered",  # L
        "wind_direction_deg",  # °T
        "wind_direction_deg_snap",
        "wind_speed_mps",  # m/s
        "wind_speed_mps_snap",  # from snapshot
        "zone",
    )

    def __init__(self, node_id: str, node_type: str, capabilities: list[str]) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.capabilities = capabilities
        self.status = REGISTERED
        self.zone: str | None = None
        self.location: list[float] | None = None  # [lat_deg, lon_deg]
        self.announced_at: float | None = None
        self.last_seen = time.time()

        self.task: str | None = None
        self.connectivity: str | None = None
        self.altitude_m_amsl: float | None = None
        self.battery_wh: float | None = None
        self.battery_pct: float | None = None
        self.payload_litres: float | None = None
        self.payload_kg: float | None = None

        self.thermal_peak_temp_c: float | None = None
        self.thermal_coverage_pct: float | None = None
        self.smoke_density_mg_m3: float | None = None
        self.smoke_optical_density: float | None = None
        self.flame_height_m: float | None = None
        self.wind_speed_mps: float | None = None
        self.wind_direction_deg: float | None = None
        self.distance_to_flame_m: float | None = None
        self.perimeter_estimate_m: float | None = None

        self.litres_delivered: float | None = None
        self.suppression_effectiveness_pct: float | None = None
        self.drop_passes: int | None = None
        self.pump_active: bool | None = None

        self.active_drones: int | None = None
        self.lost_drones: int | None = None
        self.avg_battery_pct: float | None = None
        self.min_battery_wh: float | None = None
        self.avg_payload_litres: float | None = None
        self.total_litres_delivered: float | None = None
        self.fire_id: str | None = None
        self.fire_intensity: str | None = None
        self.swarm_status: str | None = None
        self.suppression_pct: float | None = None
        self.spread_rate: str | None = None
        self.perimeter_estimate: float | None = None
        self.wind_speed_mps_snap: float | None = None
        self.wind_direction_deg_snap: float | None = None

        self.is_active_commander: bool | None = None
        self.commander_term: int | None = None

    def apply_announcement(self, ann: NodeAnnouncement) -> None:
        self.node_type = ann.node_type
        self.capabilities = list(ann.capabilities)
        self.status = ann.status
        self.zone = ann.zone
        # location from announcement stays as home position (lat, lon)
        self.location = list(ann.location) if ann.location else None
        self.announced_at = ann.announced_at
        self.last_seen = time.time()

    def apply_telemetry(self, t: DroneTelemetry) -> None:
        # position: (lat_deg, lon_deg) WGS-84
        self.location = list(t.position)  # [lat_deg, lon_deg]
        self.altitude_m_amsl = t.altitude_m_amsl
        self.task = t.task
        self.connectivity = t.connectivity
        self.battery_wh = t.battery_wh
        self.battery_pct = t.battery_pct
        self.payload_litres = t.payload_litres
        self.payload_kg = t.payload_kg
        # Scout sensors
        self.thermal_peak_temp_c = t.thermal_peak_temp_c
        self.thermal_coverage_pct = t.thermal_coverage_pct
        self.smoke_density_mg_m3 = t.smoke_density_mg_m3
        self.smoke_optical_density = t.smoke_optical_density
        self.flame_height_m = t.flame_height_m
        self.wind_speed_mps = t.wind_speed_mps
        self.wind_direction_deg = t.wind_direction_deg
        self.distance_to_flame_m = t.distance_to_flame_m
        self.perimeter_estimate_m = t.perimeter_estimate_m
        # Firefighter metrics
        self.litres_delivered = t.litres_delivered
        self.suppression_effectiveness_pct = t.suppression_effectiveness_pct
        self.drop_passes = t.drop_passes
        self.pump_active = t.pump_active
        self.last_seen = time.time()

    def apply_swarm_status(self, s: SwarmStatusSnapshot) -> None:
        self.active_drones = s.active_drones
        self.lost_drones = s.lost_drones
        self.avg_battery_pct = s.avg_battery_pct
        self.min_battery_wh = s.min_battery_wh
        self.avg_payload_litres = s.avg_payload_litres
        self.total_litres_delivered = s.total_litres_delivered
        self.fire_id = s.fire_id
        self.fire_intensity = s.fire_intensity
        self.swarm_status = s.status
        self.suppression_pct = s.suppression_pct
        self.spread_rate = s.spread_rate
        self.perimeter_estimate = s.perimeter_estimate_m
        self.wind_speed_mps_snap = s.wind_speed_mps
        self.wind_direction_deg_snap = s.wind_direction_deg
        self.last_seen = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}


class FireRecord:
    """Mutable dashboard view of a single fire event."""

    __slots__ = (
        "event_type",
        "fire_id",
        "fire_intensity",
        "fire_status",
        "leader_id",
        "location_coords",  # [lat_deg, lon_deg] WGS-84 or None
        "perimeter_m",
        "sensor_id",
        "severity",
        "source",
        "spread_rate",
        "ts",
        "wind_speed_mps",
        "zone",
    )

    def __init__(self, fire_id: str) -> None:
        self.fire_id = fire_id
        self.zone: str | None = None
        self.severity: str | None = None
        self.sensor_id: str | None = None
        self.location_coords: list[float] | None = None
        self.event_type: str | None = None
        self.source: str | None = None
        self.fire_status: str = FIRE_ACTIVE
        self.fire_intensity: str | None = None
        self.perimeter_m: float | None = None
        self.spread_rate: str | None = None
        self.wind_speed_mps: float | None = None
        self.leader_id: str | None = None
        self.ts = time.time()

    def apply_fire_event(self, ev: FireEvent) -> None:
        self.event_type = ev.event_type
        self.source = ev.source
        self.ts = ev.timestamp
        p = ev.payload
        self.zone = p.zone
        self.severity = p.severity
        self.sensor_id = p.sensor_id
        self.location_coords = list(p.location_coords) if p.location_coords else None
        if ev.event_type == FIRE_SUPPRESSED:
            self.fire_status = SUPPRESSED
        elif ev.event_type == FIRE_CONTAINED:
            self.fire_status = CONTAINED

    def apply_intensity_update(self, upd: FireIntensityUpdate) -> None:
        self.fire_intensity = upd.new_intensity
        self.perimeter_m = upd.perimeter_m
        self.spread_rate = upd.spread_rate
        self.wind_speed_mps = upd.wind_speed_mps
        self.leader_id = upd.leader_id
        self.ts = upd.timestamp

    def as_dict(self) -> dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}


class SwarmState:
    """Thread-safe in-memory state for the entire swarm."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[str, NodeState] = {}
        self._fires: dict[str, FireRecord] = {}
        self._events: list[dict[str, Any]] = []
        self._election_events: list[dict[str, Any]] = []
        self._pending_approvals: list[dict[str, Any]] = []

    def apply_announcement(self, ann: NodeAnnouncement) -> None:
        with self._lock:
            node = self._nodes.get(ann.node_id)
            if node is None:
                node = NodeState(ann.node_id, ann.node_type, list(ann.capabilities))
                self._nodes[ann.node_id] = node
            node.apply_announcement(ann)

    def apply_telemetry(self, t: DroneTelemetry) -> None:
        with self._lock:
            node = self._nodes.get(t.drone_id)
            if node is None:
                return
            node.apply_telemetry(t)

    def apply_swarm_status(self, leader_id: str, s: SwarmStatusSnapshot) -> None:
        with self._lock:
            node = self._nodes.get(leader_id)
            if node is None:
                return
            node.apply_swarm_status(s)

    def mark_offline(self, node_id: str) -> None:
        with self._lock:
            n = self._nodes.get(node_id)
            if n:
                n.status = OFFLINE

    def apply_commander_snapshot(self, node_id: str, is_active: bool, term: int | None) -> None:
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                from wfc_shared.enums.node_types import CENTRAL_COMMANDER

                node = NodeState(node_id, CENTRAL_COMMANDER, [])
                self._nodes[node_id] = node
            node.is_active_commander = is_active
            node.commander_term = term
            node.status = ACTIVE
            node.last_seen = time.time()

    def get_all_nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [n.as_dict() for n in self._nodes.values()]

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            n = self._nodes.get(node_id)
            return n.as_dict() if n else None

    def apply_fire_event(self, ev: FireEvent) -> None:
        with self._lock:
            fire_id = ev.payload.fire_id
            rec = self._fires.get(fire_id)
            if rec is None:
                rec = FireRecord(fire_id)
                self._fires[fire_id] = rec
            rec.apply_fire_event(ev)

    def apply_intensity_update(self, upd: FireIntensityUpdate) -> None:
        with self._lock:
            rec = self._fires.get(upd.fire_id)
            if rec is None:
                rec = FireRecord(upd.fire_id)
                self._fires[upd.fire_id] = rec
            rec.apply_intensity_update(upd)

    def upsert_fire_from_snapshot(self, fire_id: str, leader_id: str, fire_intensity: str) -> None:
        with self._lock:
            rec = self._fires.get(fire_id)
            if rec is None:
                rec = FireRecord(fire_id)
                self._fires[fire_id] = rec
            rec.leader_id = leader_id
            rec.fire_intensity = fire_intensity
            rec.ts = time.time()

    def get_all_fires(self) -> list[dict[str, Any]]:
        with self._lock:
            return [f.as_dict() for f in self._fires.values()]

    def add_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append({**event, "ts": time.time()})
            if len(self._events) > 300:
                self._events.pop(0)

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events[-limit:])

    def add_election_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._election_events.append({**event, "ts": time.time()})
            if len(self._election_events) > 50:
                self._election_events.pop(0)

    def get_election_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._election_events)

    def add_pending_approval(self, raw: dict[str, Any]) -> None:
        with self._lock:
            self._pending_approvals.append({**raw, "ts": time.time()})
            if len(self._pending_approvals) > 50:
                self._pending_approvals.pop(0)

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._pending_approvals)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "nodes": [n.as_dict() for n in self._nodes.values()],
                "fires": [f.as_dict() for f in self._fires.values()],
                "events": list(self._events[-20:]),
                "ts": time.time(),
            }

    def update_heartbeat(self, node_id: str, node_type: str = "UNKNOWN") -> None:
        """Update a node's status to ONLINE and refresh last_seen, leaving other fields unchanged."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                node = NodeState(node_id, node_type, [])
                self._nodes[node_id] = node
            node.status = ACTIVE
            node.last_seen = time.time()
            # Do NOT change zone, location, capabilities, etc.


swarm_state: Final[SwarmState] = SwarmState()
