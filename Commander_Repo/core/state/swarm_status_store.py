from __future__ import annotations

import threading
import time
from wfc_shared.schemas.telemetry import SwarmStatusSnapshot

class SwarmStatusStore:
    """In-memory store of the most recent SwarmStatusSnapshot per leader."""

    def __init__(self) -> None:
        self._snapshots: dict[str, SwarmStatusSnapshot] = {}
        self._lock = threading.Lock()

    def update(self, snapshot: SwarmStatusSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.leader_id] = snapshot

    def get(self, leader_id: str) -> (SwarmStatusSnapshot | None):
        with self._lock:
            return self._snapshots.get(leader_id)

    def get_for_fire(self, fire_id: str) -> list[SwarmStatusSnapshot]:
        with self._lock:
            return [s for s in self._snapshots.values() if s.fire_id == fire_id]

    def get_all_active_dict(self) -> dict[str, SwarmStatusSnapshot]:
        """Return a dict mapping leader_id -> snapshot for all active (with fire_id) leaders."""
        with self._lock:
            return {s.leader_id: s for s in self._snapshots.values() if s.fire_id is not None}

    def is_stale(self, leader_id: str, max_age_seconds: float = 30.0) -> bool:
        snap = self.get(leader_id)
        if snap is None:
            return True
        return time.time() - snap.timestamp > max_age_seconds
