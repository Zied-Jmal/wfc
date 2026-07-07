"""mission_repo.py
MissionRepository - SQLite persistence for MissionRecord
- Upsert MissionRecord rows on every state transition
- Load all mission records on startup (hydration)
- Translate between MissionRecord (dataclass) and SQL
Design:
- One row per mission_id - snapshot model, not event log
- ON CONFLICT(mission_id) DO UPDATE overwrites each call
- Mirrors FireStateRepository pattern exactly
"""

from __future__ import annotations
from typing import Any

import json

from core.persistence.database import Database
from core.state.mission_store import MissionRecord

class MissionRepository:

    def __init__(self, db: Database)  -> None:
        self._db = db

    # region  WRITE

    def upsert(self, rec: MissionRecord) -> None:
        """Insert or overwrite the row for rec.mission_id.

        Called by MissionStore on every create() and transition() -
        snapshot model, not event log.
        """
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO missions (
                mission_id, fire_id, state, assigned_node,
                created_at, updated_at, history_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                state         = excluded.state,
                assigned_node = excluded.assigned_node,
                updated_at    = excluded.updated_at,
                history_json  = excluded.history_json
            """,
            (
                rec.mission_id, rec.fire_id, rec.state, rec.assigned_node,
                rec.created_at, rec.updated_at,
                json.dumps(rec.history),
            ),
        )

    # endregion

    # region  READ

    def get_all(self) -> list[MissionRecord]:
        """Load all persisted mission records - called on startup to
        hydrate MissionStore.
        """
        rows = self._db.query("SELECT * FROM missions")  # pyright: ignore[reportUnknownMemberType]
        return [self._row_to_record(r) for r in rows]

    # endregion

    # region  EVICTION

    def delete_terminal(self, max_age_seconds: float) -> int:
        """Delete COMPLETED and FAILED missions older than max_age_seconds.
        Returns the number of rows deleted.
        """
        import time
        cutoff = time.time() - max_age_seconds
        cur = self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            DELETE FROM missions
            WHERE state IN ('COMPLETED', 'FAILED')
              AND updated_at < ?
            """,
            (cutoff,),
        )
        return cur.rowcount

    # endregion

    # region  PRIVATE

    @staticmethod
    def _row_to_record(row: Any) -> MissionRecord:
        return MissionRecord(
            mission_id=row["mission_id"],
            fire_id=row["fire_id"],
            state=row["state"],
            assigned_node=row["assigned_node"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            history=json.loads(row["history_json"] or "[]"),
        )

    # endregion
