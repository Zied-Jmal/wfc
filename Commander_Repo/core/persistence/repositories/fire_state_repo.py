# core/persistence/repositories/fire_state_repo.py
# NEW: SQLite persistence for FireRecord (state snapshots).
# DIFFERENT from fire_event_repo - this stores CURRENT state,
# one row per fire, overwritten on every transition (snapshot
"""fire_state_repo.py
FireStateRepository - SQLite persistence for FireRecord
- Upsert FireRecord rows on every state transition
- Load all fire records on CentralNode startup
- Translate between FireRecord (dataclass) and SQL rows
Design:
- One row per fire_id - snapshot model, not event log
- ON CONFLICT(fire_id) DO UPDATE overwrites on every call
- Separate from fire_events table (immutable event log)
"""

from __future__ import annotations

# Standard Library
import json
from typing import Any

# Project Imports
from core.persistence.database import Database
from core.state.fire_state_store import FireRecord

# region  CLASS - FireStateRepository


class FireStateRepository:
    """SQLite persistence layer for FireRecord objects.

    Uses a snapshot model: one row per fire_id, overwritten on
    every transition. NOT an event log - use FireEventRepository
    for the append-only event history.
    """

    # region  INITIALISATION

    def __init__(self, db: Database) -> None:
        self._db = db

    # endregion

    # region  WRITE

    def upsert(self, rec: FireRecord) -> None:
        """Insert or overwrite the row for rec.fire_id.

        Called by FireStateStore on every transition - implements the
        snapshot model (current truth, not event log). The history_json
        column stores the full audit trail as JSON alongside the current state.
        """
        coords = rec.location_coords or (None, None)
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO fire_states (
                fire_id, state, zone, severity, sensor_id,
                location_x, location_y,
                assigned_node, mission_id,
                created_at, updated_at, history_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fire_id) DO UPDATE SET
                state         = excluded.state,
                severity      = excluded.severity,
                assigned_node = excluded.assigned_node,
                mission_id    = excluded.mission_id,
                updated_at    = excluded.updated_at,
                history_json  = excluded.history_json,
                zone          = excluded.zone,
                sensor_id     = excluded.sensor_id,
                location_x    = excluded.location_x,
                location_y    = excluded.location_y
            """,
            (
                rec.fire_id,
                rec.state,
                rec.zone,
                rec.severity,
                rec.sensor_id,
                coords[0],
                coords[1],
                rec.assigned_node,
                rec.mission_id,
                rec.created_at,
                rec.updated_at,
                json.dumps(rec.history),
            ),
        )

    # endregion

    # region  READ

    def get_all(self) -> list[FireRecord]:
        """Load all persisted fire records - called on startup to hydrate FireStateStore."""
        rows = self._db.query("SELECT * FROM fire_states")  # pyright: ignore[reportUnknownMemberType]
        return [self._row_to_record(r) for r in rows]

    def get(self, fire_id: str) -> FireRecord | None:
        """Return the current FireRecord for fire_id, or None if not persisted."""
        row = self._db.query_one(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM fire_states WHERE fire_id = ?", (fire_id,)
        )
        return self._row_to_record(row) if row else None

    # endregion

    # region  PRIVATE METHODS

    @staticmethod
    def _row_to_record(row: Any) -> FireRecord:
        """Reconstruct a FireRecord from a SQLite row dict."""
        loc = None
        if row["location_x"] is not None and row["location_y"] is not None:
            loc = (row["location_x"], row["location_y"])
        return FireRecord(
            fire_id=row["fire_id"],
            state=row["state"],
            zone=row["zone"],
            severity=row["severity"],
            sensor_id=row["sensor_id"],
            location_coords=loc,
            assigned_nodes=[row["assigned_node"]] if row["assigned_node"] else [],
            mission_id=row["mission_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            history=json.loads(row["history_json"] or "[]"),
        )

    # endregion


# endregion (end of class FireStateRepository)
