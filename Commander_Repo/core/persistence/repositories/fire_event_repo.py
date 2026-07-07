# core/persistence/repositories/fire_event_repo.py
"""fire_event_repo.py
FireEventRepository - append-only fire event log
- Persist every FireEvent (DETECTED/SUPPRESSED/CONTAINED)
- Provide history queries for the digital twin
"""

from __future__ import annotations
from typing import Any

import json

from core.persistence.database import Database
from wfc_shared.schemas.events import FireEvent

class FireEventRepository:

    """Append-only log of every fire event (DETECTED/CONTAINED/SUPPRESSED).

    Different from FireStateRepository - this is the immutable historical
    record. FireStateRepository stores current truth (snapshot model).
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, event: FireEvent) -> None:
        """Append a FireEvent to the log. Always inserts - never updates.

        Stores the full payload_json alongside indexed columns so the
        dashboard and digital-twin can query by fire_id, severity, etc.
        """
        payload = event.payload
        coords = payload.location_coords or (None, None)
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO fire_events (
                fire_id, event_type, source, severity,
                location, location_x, location_y, sensor_id,
                timestamp, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.fire_id,
                event.event_type,
                event.source,
                payload.severity,
                payload.location,
                coords[0],
                coords[1],
                payload.sensor_id,
                event.timestamp,
                event.model_dump_json(),
            ),
        )

    def get_by_fire_id(self, fire_id: str) -> list[dict[str, Any]]:
        """Return all events for a given fire, ordered oldest-first."""
        rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM fire_events WHERE fire_id = ? ORDER BY timestamp ASC",
            (fire_id,),
        )
        return [self._row_to_dict(r) for r in rows]

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent `limit` events across all fires."""
        rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM fire_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(r) for r in rows]

    def get_all(self) -> list[dict[str, Any]]:
        """Return every event in the log, ordered oldest-first."""
        rows = self._db.query("SELECT * FROM fire_events ORDER BY timestamp ASC")  # pyright: ignore[reportUnknownMemberType]
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a SQLite row to the canonical fire-event dict shape."""
        return {
            "id":          row["id"],
            "fire_id":     row["fire_id"],
            "event_type":  row["event_type"],
            "source":      row["source"],
            "severity":    row["severity"],
            "location":    row["location"],
            "location_coords": (
                (row["location_x"], row["location_y"])
                if row["location_x"] is not None else None
            ),
            "sensor_id":   row["sensor_id"],
            "timestamp":   row["timestamp"],
            "payload":     json.loads(row["payload_json"]),
        }
