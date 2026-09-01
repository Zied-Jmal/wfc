from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.persistence.database import Database
from core.utils.logger import log
from wfc_shared.enums.fire_status import FIRE_TRANSITIONS, IGNITED, TERMINAL_FIRE_STATES

# region  SCHEMA - FireRecord


class FireRecord(BaseModel):
    """Single source of truth for one fire event.

    assigned_nodes (list) for multi-leader support.
    leader_term added (int, default 0) for bully election tracking.
    @property assigned_node bridges old code to list[0].
    """

    model_config = ConfigDict(frozen=True)

    fire_id: str
    state: str
    zone: str
    severity: str
    sensor_id: str
    location_coords: tuple[float, float] | None = None

    # single node list of nodes
    assigned_nodes: list[str] = Field(default_factory=list)

    # bully election term tracking
    leader_term: int = 0

    mission_id: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    history: tuple[dict[str, Any], ...] = Field(default_factory=tuple)

    # BRIDGE: @property assigned_node (backward-compatible)
    @property
    def assigned_node(self) -> str | None:
        """Backward-compatible bridge: returns first node in list, or None."""
        return self.assigned_nodes[0] if self.assigned_nodes else None

    # region  HELPERS

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_FIRE_STATES

    def snapshot(self) -> dict[str, Any]:
        """Current state as plain dict for MQTT publication."""
        return {
            "fire_id": self.fire_id,
            "state": self.state,
            "zone": self.zone,
            "severity": self.severity,
            "sensor_id": self.sensor_id,
            "location_coords": list(self.location_coords) if self.location_coords else None,
            "assigned_nodes": self.assigned_nodes,
            "leader_term": self.leader_term,
            "mission_id": self.mission_id,
            "updated_at": self.updated_at,
        }


# endregion

# endregion


# region CLASS - FireStateStore
class FireStateStore:
    """In-memory fire state store with optional SQLite persistence.

    Manages the lifecycle of FireRecord objects through state transitions.
    Only this class may call transition() — external code reads state via get().
    """

    def __init__(self, db: Database | None = None) -> None:
        """Initialize the fire state store.

        Args:
            db: Optional Database instance for SQLite persistence.
                If provided, state is hydrated from disk on startup.
        """
        self._fires: dict[str, FireRecord] = {}
        self._repo = None
        if db is not None:
            from core.persistence.repositories.fire_state_repo import FireStateRepository

            self._repo = FireStateRepository(db)
            for rec in self._repo.get_all():
                self._fires[rec.fire_id] = rec
            if self._fires:
                log("FireStateStore", f"hydrated {len(self._fires)} fire(s) from database", channel="STATE")

    # region PUBLIC API
    def ignite(
        self,
        fire_id: str,
        zone: str,
        severity: str,
        sensor_id: str,
        location_coords: (tuple[float, float] | None) = None,
    ) -> FireRecord:
        """Create a new fire in IGNITED state, or return existing if fire_id known.

        Args:
            fire_id: Globally unique fire identifier.
            zone: Zone label (e.g. "zone_alpha").
            severity: Fire severity (LOW|MEDIUM|HIGH|CRITICAL).
            sensor_id: Node_id of the detecting sensor.
            location_coords: Optional (lat_deg, lon_deg) WGS-84.

        Returns:
            The FireRecord (newly created or existing).
        """
        if fire_id in self._fires:
            log(
                "FireStateStore",
                f"ignite() called for existing fire {fire_id[:8]} - returning current state",
                channel="STATE",
            )
            return self._fires[fire_id]
        rec = FireRecord(
            fire_id=fire_id,
            state=IGNITED,
            zone=zone,
            severity=severity,
            sensor_id=sensor_id,
            location_coords=location_coords,
            history=(self._make_history_entry(IGNITED, "fire_detected"),),
        )
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        log("FireStateStore", f"fire IGNITED: {fire_id[:8]} zone={zone} severity={severity}", channel="STATE")
        return rec

    def transition(
        self,
        fire_id: str,
        new_state: str,
        reason: str = "",
        assigned_node: (str | None) = None,
        mission_id: (str | None) = None,
    ) -> FireRecord | None:
        """Transition a fire to a new state if the move is valid.

        Args:
            fire_id: The fire to transition.
            new_state: Target state (must be in FIRE_TRANSITIONS[current]).
            reason: Free-text reason for the transition (logged in history).
            assigned_node: Optional node_id to add to assigned_nodes list.
            mission_id: Optional mission_id to assign.

        Returns:
            Updated FireRecord if transition succeeded, current record if
            terminal, or None if fire unknown or transition invalid.
        """
        rec = self._fires.get(fire_id)
        if rec is None:
            log("FireStateStore", f"transition() for unknown fire {fire_id[:8]}", channel="STATE")
            return None
        if rec.is_terminal():
            log(
                "FireStateStore", f"fire {fire_id[:8]} is terminal ({rec.state}) - transition rejected", channel="STATE"
            )
            return rec
        allowed = FIRE_TRANSITIONS.get(rec.state, set())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        if new_state not in allowed:
            log("FireStateStore", f"INVALID fire transition {rec.state}{new_state} for {fire_id[:8]}", channel="STATE")
            return None
        old_state = rec.state
        now = time.time()
        new_history = (*rec.history, self._make_history_entry(new_state, reason))
        # assigned_node updates go into assigned_nodes list
        new_nodes = list(rec.assigned_nodes)
        if assigned_node is not None and assigned_node not in new_nodes:
            new_nodes.append(assigned_node)
            # If assigned_node is None, we keep the current list (do NOT clear)
        updates: dict[str, Any] = {
            "state": new_state,
            "updated_at": now,
            "history": new_history,
            "assigned_nodes": new_nodes,
        }
        if mission_id is not None:
            updates["mission_id"] = mission_id
        rec = rec.model_copy(update=updates)
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        log("FireStateStore", f"fire {fire_id[:8]}: {old_state} {new_state} reason={reason}", channel="STATE")
        return rec

    def assign_node(
        self,
        fire_id: str,
        assigned_node: (str | None),
        reason: str = "",
    ) -> FireRecord | None:
        """Updates assigned_nodes list. If assigned_node is provided,
        append it if not already present. If assigned_node is None, clear the list.
        """
        rec = self._fires.get(fire_id)
        if rec is None:
            log("FireStateStore", f"assign_node() for unknown fire {fire_id[:8]}", channel="STATE")
            return None

        old_nodes = rec.assigned_nodes
        new_nodes = list(old_nodes)

        if assigned_node is None:
            new_nodes = []
        else:
            if assigned_node not in new_nodes:
                new_nodes.append(assigned_node)

        new_history = (*rec.history, self._make_history_entry(rec.state, reason or "assigned_node_changed"))
        rec = rec.model_copy(
            update={
                "assigned_nodes": new_nodes,
                "updated_at": time.time(),
                "history": new_history,
            }
        )
        self._fires[fire_id] = rec

        if self._repo is not None:
            self._repo.upsert(rec)

        log(
            "FireStateStore",
            f"fire {fire_id[:8]}: assigned_nodes {old_nodes} → {new_nodes} reason={reason}",
            channel="STATE",
        )
        return rec

    def add_assigned_node(self, fire_id: str, node_id: str, reason: str = "") -> FireRecord | None:
        """Convenience: add a node to assigned_nodes."""
        return self.assign_node(fire_id, node_id, reason)

    def remove_assigned_node(self, fire_id: str, node_id: str, reason: str = "") -> FireRecord | None:
        """Convenience: remove a node from assigned_nodes."""
        rec = self._fires.get(fire_id)
        if rec is None:
            return None
        new_nodes = [n for n in rec.assigned_nodes if n != node_id]
        if len(new_nodes) == len(rec.assigned_nodes):
            return rec  # no change
        new_history = (*rec.history, self._make_history_entry(rec.state, reason or "remove_assigned_node"))
        rec = rec.model_copy(
            update={
                "assigned_nodes": new_nodes,
                "updated_at": time.time(),
                "history": new_history,
            }
        )
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        return rec

    def update_severity(self, fire_id: str, new_severity: str, reason: str = "") -> FireRecord | None:
        """Update severity without changing state.

        Args:
            fire_id: The fire to update.
            new_severity: New severity level.
            reason: Free-text reason for the change.

        Returns:
            Updated FireRecord if found, None otherwise.
        """
        rec = self._fires.get(fire_id)
        if rec is None:
            return None
        if rec.severity == new_severity:
            return rec  # no change
        new_history = (*rec.history, self._make_history_entry(rec.state, reason or "severity_update"))
        rec = rec.model_copy(
            update={
                "severity": new_severity,
                "updated_at": time.time(),
                "history": new_history,
            }
        )
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        log("FireStateStore", f"fire {fire_id[:8]}: severity {rec.severity} → {new_severity}", channel="STATE")
        return rec

    def update_leader_term(self, fire_id: str, new_term: int, reason: str = "") -> FireRecord | None:
        """Update leader_term without changing state.

        Args:
            fire_id: The fire to update.
            new_term: New leader term value.
            reason: Free-text reason for the change.

        Returns:
            Updated FireRecord if found, None otherwise.
        """
        rec = self._fires.get(fire_id)
        if rec is None:
            return None
        if rec.leader_term == new_term:
            return rec  # no change
        new_history = (*rec.history, self._make_history_entry(rec.state, reason or "leader_term_update"))
        rec = rec.model_copy(
            update={
                "leader_term": new_term,
                "updated_at": time.time(),
                "history": new_history,
            }
        )
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        log("FireStateStore", f"fire {fire_id[:8]}: leader_term {rec.leader_term} → {new_term}", channel="STATE")
        return rec

    def get(self, fire_id: str) -> FireRecord | None:
        """Return the FireRecord for fire_id, or None if not found.

        Args:
            fire_id: The fire identifier to look up.

        Returns:
            FireRecord if found, None otherwise.
        """
        return self._fires.get(fire_id)

    def get_active(self) -> list[FireRecord]:
        """Return all non-terminal fire records.

        Returns:
            List of FireRecord objects in non-terminal states.
        """
        return [r for r in self._fires.values() if not r.is_terminal()]

    def get_all(self) -> dict[str, FireRecord]:
        """Return a snapshot copy of all fire records (all states).

        Returns:
            Dict mapping fire_id to FireRecord.
        """
        return dict(self._fires)

    def snapshot_all(self) -> list[dict[str, Any]]:
        """Return all fires as plain dicts for MQTT publication."""
        return [r.snapshot() for r in self.get_active()]

    def apply_snapshot_record(self, fire_id: str, data: dict[str, Any]) -> bool:
        """
        LWW merge now handles assigned_nodes (list union) and leader_term.
        """
        existing = self._fires.get(fire_id)
        incoming_updated_at = data.get("updated_at", 0.0)

        if existing is not None and existing.updated_at >= incoming_updated_at:
            return False

        # Merge assigned_nodes: union of existing and incoming, with dedupe.
        existing_nodes = existing.assigned_nodes if existing else []
        incoming_nodes = data.get("assigned_nodes", [])
        merged_nodes = list(dict.fromkeys(existing_nodes + incoming_nodes))

        incoming_term = data.get("leader_term", 0)
        current_term = existing.leader_term if existing else 0
        merged_term = max(incoming_term, current_term)

        loc = data.get("location_coords")
        rec = FireRecord(
            fire_id=fire_id,
            state=data.get("state"),  # pyright: ignore[reportArgumentType]
            zone=data.get("zone"),  # pyright: ignore[reportArgumentType]
            severity=data.get("severity"),  # pyright: ignore[reportArgumentType]
            sensor_id=data.get("sensor_id"),  # pyright: ignore[reportArgumentType]
            location_coords=tuple(loc) if loc else None,
            assigned_nodes=merged_nodes,
            leader_term=merged_term,
            mission_id=data.get("mission_id"),
            updated_at=incoming_updated_at or time.time(),
            created_at=existing.created_at if existing else time.time(),
            history=existing.history if existing else (),
        )
        self._fires[fire_id] = rec
        if self._repo is not None:
            self._repo.upsert(rec)
        return True

    # endregion

    @staticmethod
    def _make_history_entry(state: str, reason: str) -> dict[str, Any]:
        return {"state": state, "reason": reason, "timestamp": time.time()}


# endregion
