# core/state/projector.py
# Added F8 timestamp guards to all state-change handlers.
# Rejects stale events where event.timestamp <= fire.updated_at.
# Also handles assigned_nodes list for dispatched events.
from __future__ import annotations
from typing import Any

from wfc_shared.schemas.domain_event import DomainEvent
from wfc_shared.enums.domain_event_types import (
    FIRE_DETECTED,
    FIRE_CONTAINED,
    FIRE_SUPPRESSED,
    FIRE_DISPATCHED,
    FIRE_REDISPATCHED,
    LEADER_DIED,
)
from wfc_shared.enums.fire_status import ACTIVE, CONTAINED, SUPPRESSED
from wfc_shared.enums.mission_status import ASSIGNED, PAUSED
from core.utils.logger import log

class FireProjector:
    def __init__(self, fire_store: Any, mission_store: Any)  -> None:
        self._fires = fire_store
        self._missions = mission_store

    def apply(self, event: DomainEvent) -> None:
        handlers = {
            FIRE_DETECTED:     self._on_fire_detected,
            FIRE_CONTAINED:    self._on_fire_contained,
            FIRE_SUPPRESSED:   self._on_fire_suppressed,
            FIRE_DISPATCHED:   self._on_fire_dispatched,
            FIRE_REDISPATCHED: self._on_fire_redispatched,
            LEADER_DIED:       self._on_leader_died,
        }
        handler = handlers.get(event.event_type)
        if handler:
            try:
                handler(event)
            except Exception as exc:
                log("FireProjector",
                    f"apply({event.event_type}) fire={event.fire_id} failed: {exc}",
                    channel="SYSTEM")

    def replay(self, events: list[DomainEvent]) -> None:
        ordered = sorted(events, key=lambda e: (e.timestamp, e.sequence or 0))
        count = 0
        for event in ordered:
            self.apply(event)
            count += 1
        log("FireProjector",
            f"replayed {count} domain events on startup",
            channel="SYSTEM")

# HANDLERS with F8 timestamp guards

    def _on_fire_detected(self, event: DomainEvent) -> None:
        fire_id = event.fire_id
        if not fire_id:
            return
        fire = self._fires.get(fire_id)
# F8 guard: skip if our state is newer or equal
        if fire and event.timestamp <= fire.updated_at:
            return

        if fire is None:
            p = event.payload
            self._fires.ignite(
                fire_id=fire_id,
                zone=p.get("zone", "unknown"),
                severity=p.get("severity", "MEDIUM"),
                sensor_id=p.get("sensor_id", "unknown"),
                location_coords=None,
            )
            self._fires.transition(fire_id, ACTIVE, reason="replay_fire_detected")

    def _on_fire_contained(self, event: DomainEvent) -> None:
        fire_id = event.fire_id
        if not fire_id:
            return
        fire = self._fires.get(fire_id)
# F8 guard
        if fire and event.timestamp <= fire.updated_at:
            return
        if fire and fire.state not in (CONTAINED, SUPPRESSED):
            self._fires.transition(fire_id, CONTAINED, reason="replay_fire_contained")

    def _on_fire_suppressed(self, event: DomainEvent) -> None:
        fire_id = event.fire_id
        if not fire_id:
            return
        fire = self._fires.get(fire_id)
# F8 guard
        if fire and event.timestamp <= fire.updated_at:
            return
        if fire and fire.state != SUPPRESSED:
            self._fires.transition(fire_id, SUPPRESSED, reason="replay_fire_suppressed")

    def _on_fire_dispatched(self, event: DomainEvent) -> None:
        fire_id = event.fire_id
        node_id = event.node_id
        if not (fire_id and node_id):
            return
        fire = self._fires.get(fire_id)
# F8 guard
        if fire and event.timestamp <= fire.updated_at:
            return
# add to assigned_nodes list
        if fire and node_id not in fire.assigned_nodes:
            self._fires.add_assigned_node(
                fire_id, node_id, reason="replay_fire_dispatched"
            )

        mission = self._missions.get_for_fire(fire_id)
        if mission and mission.state not in (ASSIGNED,):
            try:
                self._missions.transition(
                    mission.mission_id, ASSIGNED,
                    reason="replay_fire_dispatched",
                    assigned_node=node_id,
                )
            except Exception:
                pass

    def _on_fire_redispatched(self, event: DomainEvent) -> None:
        fire_id = event.fire_id
        dead_leader = event.node_id
        if not fire_id:
            return
        fire = self._fires.get(fire_id)
# F8 guard
        if fire and event.timestamp <= fire.updated_at:
            return
        if fire and dead_leader in fire.assigned_nodes:
            self._fires.remove_assigned_node(
                fire_id, dead_leader, reason="replay_fire_redispatched"
            )

        mission = self._missions.get_for_fire(fire_id)
        if mission and mission.state not in (ASSIGNED, PAUSED):
            try:
                self._missions.transition(
                    mission.mission_id, PAUSED,
                    reason="replay_leader_died",
                )
            except Exception:
                pass

    def _on_leader_died(self, event: DomainEvent) -> None:
# Handled by FIRE_REDISPATCHED; no action needed here.
        pass
