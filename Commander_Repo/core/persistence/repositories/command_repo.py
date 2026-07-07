# core/persistence/repositories/command_repo.py
# SQLite-backed persistence for CommandTracker records.
"""command_repo.py
CommandRepository - persistence for command lifecycle
records and their audit history
- Persist per-trace command status
- Append-only audit history per command
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.persistence.database import Database

class CommandRepository:

    """Persistence layer for CommandTracker records.

    Two tables: `commands` (one row per trace_id, snapshot of current
    status) and `command_history` (append-only event log per command).
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_command(self, trace_id: str, command: dict[str, Any], status: str) -> None:
        """Insert a new command record or update its status if it already exists."""
        now = time.time()
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO commands (trace_id, command_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                status     = excluded.status,
                updated_at = excluded.updated_at
            """,
            (trace_id, json.dumps(command), status, now, now),
        )

    def append_history(self, trace_id: str, event_type: str, payload: dict[str, Any], timestamp: float) -> None:
        """Append one audit event to the command_history table for trace_id."""
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO command_history (trace_id, event_type, payload_json, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (trace_id, event_type, json.dumps(payload), timestamp),
        )

    def update_status(self, trace_id: str, status: str) -> None:
        """Update only the status column for trace_id - lighter than a full upsert."""
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            "UPDATE commands SET status = ?, updated_at = ? WHERE trace_id = ?",
            (status, time.time(), trace_id),
        )

    def get(self, trace_id: str) -> dict[str, Any] | None:
        """Return command dict + history list for trace_id, or None if not found."""
        row = self._db.query_one("SELECT * FROM commands WHERE trace_id = ?", (trace_id,))  # pyright: ignore[reportUnknownMemberType]
        if row is None:
            return None
        history_rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT event_type, payload_json, timestamp FROM command_history "
            "WHERE trace_id = ? ORDER BY id ASC",
            (trace_id,),
        )
        history = [
            {
                "type":      h["event_type"],
                "payload":   json.loads(h["payload_json"]),
                "timestamp": h["timestamp"],
            }
            for h in history_rows
        ]
        return {
            "command": json.loads(row["command_json"]),
            "status":  row["status"],
            "history": history,
        }

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Load all commands with their full history in two queries (not N+1)."""
        cmd_rows = self._db.query("SELECT * FROM commands")  # pyright: ignore[reportUnknownMemberType]
        if not cmd_rows:
            return {}

        # Build command map first
        result: dict[str, dict[str, Any]] = {}
        for row in cmd_rows:
            result[row["trace_id"]] = {
                "command": json.loads(row["command_json"]),
                "status":  row["status"],
                "history": [],
            }

        # Single query for all history rows, then group in Python
        history_rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT trace_id, event_type, payload_json, timestamp "
            "FROM command_history ORDER BY id ASC"
        )
        for h in history_rows:
            tid = h["trace_id"]
            if tid in result:
                result[tid]["history"].append({
                    "type":      h["event_type"],
                    "payload":   json.loads(h["payload_json"]),
                    "timestamp": h["timestamp"],
                })

        return result
