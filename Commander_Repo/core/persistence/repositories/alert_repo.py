# core/persistence/repositories/alert_repo.py
"""alert_repo.py
AlertRepository - persistence for control-room alerts
- Insert new alerts (HIGH fire, pending approval,
node down, command failed)
- Track acknowledgement (acked_at / acked_by)
- Provide open / recent alert queries
"""

from __future__ import annotations

# Standard Library
import time
from typing import Any

# Project Imports
from core.persistence.database import Database

# region  CLASS - AlertRepository


class AlertRepository:
    """Persistence layer for control-room alerts.

    Alerts are idempotent on alert_id - the same node-down event
    re-firing will not create a duplicate alert row.
    """

    # region  INITIALISATION

    def __init__(self, db: Database) -> None:
        self._db = db

    # endregion

    # region  WRITE

    def add(
        self,
        alert_id: str,
        kind: str,
        severity: str,
        title: str,
        detail: str | None = None,
        source_ref: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Insert a new alert. Idempotent on alert_id - a duplicate
        insert (e.g. the same node-down alert re-fired) is ignored.
        """
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO alerts (alert_id, kind, severity, title, detail, source_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_id) DO NOTHING
            """,
            (alert_id, kind, severity, title, detail, source_ref, created_at or time.time()),
        )

    def acknowledge(self, alert_id: str, acked_by: str | None = None) -> bool:
        """Mark an alert as acknowledged. Returns True if a row was updated."""
        cursor = self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            "UPDATE alerts SET acked_at = ?, acked_by = ? WHERE alert_id = ? AND acked_at IS NULL",
            (time.time(), acked_by, alert_id),
        )
        return cursor.rowcount > 0

    # endregion

    # region  READ

    def get_open(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return up to `limit` unacknowledged alerts, newest first."""
        rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM alerts WHERE acked_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(row) for row in rows]

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return up to `limit` alerts (all statuses), newest first."""
        rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_dict(row) for row in rows]

    def exists(self, alert_id: str) -> bool:
        """Return True if an alert with this alert_id has already been inserted."""
        row = self._db.query_one("SELECT 1 FROM alerts WHERE alert_id = ?", (alert_id,))  # pyright: ignore[reportUnknownMemberType]
        return row is not None

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a SQLite row to the canonical alert dict shape."""
        return {
            "alert_id": row["alert_id"],
            "kind": row["kind"],
            "severity": row["severity"],
            "title": row["title"],
            "detail": row["detail"],
            "source_ref": row["source_ref"],
            "created_at": row["created_at"],
            "acked_at": row["acked_at"],
            "acked_by": row["acked_by"],
        }

    # endregion


# endregion
