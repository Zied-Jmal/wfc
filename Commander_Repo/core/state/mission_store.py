"""mission_store.py
MissionStore - single source of truth for mission state
- Create new MissionRecord on FIRE_DETECTED
- Enforce the legal mission transition table on writes
- Index missions by fire_id for fast lookup
- Expose snapshots for MQTT state publication
Design rules enforced:
M1 - only CentralNode/dispatcher creates missions
M2 - single owner - MissionStore
M3 - mission state is authoritative; drone ACK only
informs transitions, never overrides state
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from core.utils.logger import log
from wfc_shared.enums.mission_status import (
    CREATED,
    MISSION_TRANSITIONS,
    TERMINAL_MISSION_STATES,
)

if TYPE_CHECKING:
    from core.persistence.repositories.mission_repo import MissionRepository

# region  MODEL - MissionRecord


class MissionRecord(BaseModel):
    """Single source of truth for one fire-response mission.

    Created by CentralNode on FIRE_DETECTED.
    Drone state is secondary - MissionRecord state is authoritative.
    The `history` list is an append-only audit trail, never used
    for decision-making.
    """

    mission_id: str
    fire_id: str
    state: str
    assigned_node: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    history: list[dict[str, Any]] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]

    # region  HELPERS

    def is_terminal(self) -> bool:
        """Return True if the mission is in a terminal state (COMPLETED or FAILED)."""
        return self.state in TERMINAL_MISSION_STATES

    def snapshot(self) -> dict[str, Any]:
        """Return current state as a plain dict for MQTT publication."""
        return {
            "mission_id": self.mission_id,
            "fire_id": self.fire_id,
            "state": self.state,
            "assigned_node": self.assigned_node,
            "updated_at": self.updated_at,
        }

    # endregion


# endregion

# region  CLASS - MissionStore


class MissionStore:
    """Owns the lifecycle of all fire-response missions.

    Design rules enforced here (see RULES_COMPLIANCE.md):
      M1 - only CentralNode/dispatcher creates missions via create()
      M2 - single owner - this class
      M3 - mission state is authoritative; drone ACK only informs transition
    """

    # region  INITIALISATION

    def __init__(self, db: Any = None) -> None:
        # mission_id MissionRecord
        self._missions: dict[str, MissionRecord] = {}
        # fire_id mission_id (one active mission per fire at a time)
        self._fire_index: dict[str, str] = {}
        # persistence repo - None means in-memory only (e.g. standby mirror)
        self._repo: MissionRepository | None = None

        if db is not None:
            from core.persistence.repositories.mission_repo import MissionRepository as _Repo

            self._repo = _Repo(db)  # pyright: ignore[reportUnknownArgumentType]
            self._hydrate()

    def _hydrate(self) -> None:
        """Load all persisted missions from DB on startup."""
        if self._repo is None:
            return
        records = self._repo.get_all()
        for rec in records:
            self._missions[rec.mission_id] = rec
            if rec.fire_id:
                self._fire_index[rec.fire_id] = rec.mission_id
        if records:
            log("MissionStore", f"hydrated {len(records)} mission(s) from database", channel="STATE")

    # endregion

    # region  PUBLIC API

    def create(self, fire_id: str) -> MissionRecord:
        """Create a new mission for a fire in CREATED state.

        Idempotent - if an active (non-terminal) mission already exists
        for this fire_id, the existing record is returned unchanged.
        This prevents duplicate missions when a fire event is retried.
        """
        existing_id = self._fire_index.get(fire_id)
        if existing_id:
            existing = self._missions.get(existing_id)
            if existing and not existing.is_terminal():
                log(
                    "MissionStore",
                    f"active mission already exists for fire={fire_id[:8]} mission={existing_id[:8]}",
                    channel="STATE",
                )
                return existing

        mission_id = str(uuid.uuid4())
        rec = MissionRecord(
            mission_id=mission_id,
            fire_id=fire_id,
            state=CREATED,
            history=[{"state": CREATED, "reason": "mission_created", "timestamp": time.time()}],
        )
        self._missions[mission_id] = rec
        self._fire_index[fire_id] = mission_id
        if self._repo is not None:
            self._repo.upsert(rec)
        log("MissionStore", f"mission CREATED: {mission_id[:8]} fire={fire_id[:8]}", channel="STATE")
        return rec

    def transition(
        self,
        mission_id: str,
        new_state: str,
        reason: str = "",
        assigned_node: (str | None) = None,
    ) -> MissionRecord | None:
        """Transition mission to a new state.

        Enforces the MISSION_TRANSITIONS table - invalid transitions are
        logged and return None without mutating state. Terminal missions
        (COMPLETED or FAILED) reject all further transitions.

        Args:
            mission_id:    target mission.
            new_state:     desired next state.
            reason:        human-readable label written to history.
            assigned_node: optional; sets mission.assigned_node if provided.

        Returns:
            Updated MissionRecord on success, None on invalid/unknown transition.
        """
        rec = self._missions.get(mission_id)
        if rec is None:
            log("MissionStore", f"unknown mission {mission_id[:8]}", channel="STATE")
            return None

        if rec.is_terminal():
            log("MissionStore", f"mission {mission_id[:8]} is terminal ({rec.state})", channel="STATE")
            return rec

        allowed = MISSION_TRANSITIONS.get(rec.state, set())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        if new_state not in allowed:
            log(
                "MissionStore",
                f"INVALID mission transition {rec.state}→{new_state} for {mission_id[:8]}",
                channel="STATE",
            )
            return None

        old_state = rec.state
        rec.state = new_state
        rec.updated_at = time.time()
        if assigned_node is not None:
            rec.assigned_node = assigned_node
        rec.history.append(
            {
                "state": new_state,
                "reason": reason,
                "timestamp": time.time(),
            }
        )
        if self._repo is not None:
            self._repo.upsert(rec)
        log(
            "MissionStore",
            f"mission {mission_id[:8]}: {old_state} → {new_state} reason={reason} node={assigned_node}",
            channel="STATE",
        )
        return rec

    def get(self, mission_id: str) -> MissionRecord | None:
        """Return MissionRecord for mission_id, or None if unknown."""
        return self._missions.get(mission_id)

    def get_for_fire(self, fire_id: str) -> MissionRecord | None:
        """Return the current (most recently created) mission for a fire_id."""
        mid = self._fire_index.get(fire_id)
        return self._missions.get(mid) if mid else None

    def get_active(self) -> list[MissionRecord]:
        """Return all missions that are not in a terminal state."""
        return [r for r in self._missions.values() if not r.is_terminal()]

    def evict_terminal(self, max_age_seconds: float = 3600.0) -> int:
        """
        Evict terminal missions older than max_age_seconds from the
        in-memory dict and from the DB. Called periodically from
        CommanderCore._expire_loop().

        Returns the number of missions evicted.
        """
        import time

        cutoff = time.time() - max_age_seconds
        to_evict = [m for m in self._missions.values() if m.is_terminal() and m.updated_at < cutoff]
        for rec in to_evict:
            self._missions.pop(rec.mission_id, None)
            self._fire_index.pop(rec.fire_id, None)
        if to_evict and self._repo is not None:
            self._repo.delete_terminal(max_age_seconds)
        return len(to_evict)

    def snapshot_all(self) -> list[dict[str, Any]]:
        """Return plain-dict snapshots of all active missions for MQTT publication."""
        return [r.snapshot() for r in self.get_active()]

    def apply_snapshot_record(self, mission_id: str, data: dict[str, Any]) -> bool:
        """
        Merge a snapshot dict (from wfc/state/snapshot) into the local
        MissionRecord for mission_id. Last-write-wins by `updated_at` -
        only overwritten if the incoming data is NEWER. Returns True if
        applied, False if the incoming data was stale and skipped.

        This used to unconditionally overwrite and required callers to
        only invoke it while STANDBY, which broke on lease reclaim
        (a just-reclaimed Central would erase missions Backup created
        during the outage). The merge check makes this safe to call
        regardless of active/standby state.

        Also repairs self._fire_index so get_for_fire() resolves
        correctly on the mirror - without this, a promoted backup (or
        a reclaiming Central) could create a SECOND mission for a fire
        that already has one, because get_for_fire() would return None.
        """
        existing = self._missions.get(mission_id)
        incoming_updated_at = data.get("updated_at", 0.0)

        if existing is not None and existing.updated_at >= incoming_updated_at:
            return False

        fire_id = data.get("fire_id")
        rec = MissionRecord(
            mission_id=mission_id,
            fire_id=fire_id,  # pyright: ignore[reportArgumentType]
            state=data.get("state"),  # pyright: ignore[reportArgumentType]
            assigned_node=data.get("assigned_node"),
            updated_at=incoming_updated_at or time.time(),
            created_at=existing.created_at if existing else time.time(),
            history=existing.history if existing else [],
        )
        self._missions[mission_id] = rec
        if fire_id:
            self._fire_index[fire_id] = mission_id
        return True

    # endregion


# endregion (end of class MissionStore)
