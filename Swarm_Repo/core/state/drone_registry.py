# core/state/drone_registry.py
# Tracks drones assigned to this leader. Internal only.
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


from wfc_shared.schemas.telemetry import DroneTelemetry
from core.utils.logger import log


@dataclass
class DroneRecord:
    drone_id:          str
    role:              str                      # SCOUT | FIREFIGHTING
    location:          tuple[float, float]
    last_telemetry:    DroneTelemetry | None = None
    last_seen:         float                    = field(default_factory=time.time)
    registered_at:     float                    = field(default_factory=time.time)


class DroneRegistry:
    """Internal registry of drones assigned to this leader.
    Thread-safe. The commander never sees this directly.
    """


    def __init__(self, stale_threshold: float = 5.0) -> None:
        self._lock             = threading.Lock()
        self._drones:          dict[str, DroneRecord] = {}
        self._stale_threshold  = stale_threshold

# REGISTRATION

    def register(self, drone_id: str, role: str, location: tuple[float, float]) -> None:
        with self._lock:
            if drone_id not in self._drones:
                self._drones[drone_id] = DroneRecord(
                    drone_id=drone_id,
                    role=role,
                    location=location,
                )
                log("DroneRegistry",
                    f"registered {drone_id} role={role}", channel="REGISTRY")

    def unregister(self, drone_id: str) -> None:
        with self._lock:
            if drone_id in self._drones:
                del self._drones[drone_id]
                log("DroneRegistry", f"unregistered {drone_id}", channel="REGISTRY")

# TELEMETRY UPDATE

    def update_telemetry(self, drone_id: str, telem: DroneTelemetry) -> None:
        with self._lock:
            if drone_id not in self._drones:
                if telem.thermal_peak_temp_c is not None:  # pyright: ignore[reportUnnecessaryComparison]
                    inferred_role = "SCOUT"
                elif telem.payload_litres is not None and telem.payload_litres >= 0.0:  # pyright: ignore[reportUnnecessaryComparison]
                    inferred_role = "FIREFIGHTING"
                else:
                    inferred_role = "SCOUT"
                self._drones[drone_id] = DroneRecord(
                    drone_id=drone_id,
                    role=inferred_role,
                    location=telem.position,
                )
            rec = self._drones[drone_id]
            rec.last_telemetry = telem
            rec.last_seen      = time.time()
            rec.location       = telem.position

# QUERIES

    def get_all(self) -> list[DroneRecord]:
        with self._lock:
            return list(self._drones.values())

    def get_by_role(self, role: str) -> list[DroneRecord]:
        with self._lock:
            return [d for d in self._drones.values() if d.role == role]

    def get_active(self) -> list[DroneRecord]:
        with self._lock:
            return [
                d for d in self._drones.values()
                if d.last_telemetry is None
                or d.last_telemetry.connectivity != "LOST"
            ]

    def get_lost(self) -> list[DroneRecord]:
        now = time.time()
        with self._lock:
            return [
                d for d in self._drones.values()
                if (now - d.last_seen) > self._stale_threshold
            ]

    def get_idle(self) -> list[DroneRecord]:
        with self._lock:
            return [
                d for d in self._drones.values()
                if d.last_telemetry and d.last_telemetry.task == "IDLE"
            ]

    def get(self, drone_id: str) -> DroneRecord | None:
        with self._lock:
            return self._drones.get(drone_id)

    def count(self) -> int:
        with self._lock:
            return len(self._drones)
