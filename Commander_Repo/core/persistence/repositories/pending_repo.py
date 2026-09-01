# core/persistence/repositories/pending_repo.py
"""pending_repo.py
PendingRepository - persistence for PendingCommand rows
- Upsert / load pending approval commands
- Survive restarts: operators don't lose pending approvals
"""

from __future__ import annotations

import json
from typing import Any

from core.persistence.database import Database
from wfc_shared.schemas.commands import Command
from wfc_shared.schemas.pending import PendingCommand


class PendingRepository:
    """Persistence layer for PendingCommand rows (approval pipeline).

    Ensures operators don't lose pending approval items across
    CentralNode restarts - items are hydrated on startup.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, pending: PendingCommand) -> None:
        """Insert or update a PendingCommand row."""
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO pending_commands (
                pending_id, command_json, status,
                created_at, expires_at, decided_at, operator_id, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pending_id) DO UPDATE SET
                command_json = excluded.command_json,
                status       = excluded.status,
                expires_at   = excluded.expires_at,
                decided_at   = excluded.decided_at,
                operator_id  = excluded.operator_id,
                reason       = excluded.reason
            """,
            (
                pending.pending_id,
                pending.command.model_dump_json(),
                pending.status,
                pending.created_at,
                pending.expires_at,
                pending.decided_at,
                pending.operator_id,
                pending.reason,
            ),
        )

    def get(self, pending_id: str) -> PendingCommand | None:
        """Return PendingCommand for pending_id, or None if not found."""
        row = self._db.query_one(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM pending_commands WHERE pending_id = ?", (pending_id,)
        )
        if row is None:
            return None
        return self._row_to_pending(row)

    def get_all(self) -> list[PendingCommand]:
        """Return all pending commands (any status) - used on startup to hydrate store."""
        rows = self._db.query("SELECT * FROM pending_commands")  # pyright: ignore[reportUnknownMemberType]
        return [self._row_to_pending(r) for r in rows]

    def get_all_pending(self) -> list[PendingCommand]:
        """Return only commands with status=PENDING - active approval queue."""
        rows = self._db.query(  # pyright: ignore[reportUnknownMemberType]
            "SELECT * FROM pending_commands WHERE status = 'PENDING'"
        )
        return [self._row_to_pending(r) for r in rows]

    @staticmethod
    def _row_to_pending(row: Any) -> PendingCommand:
        """Reconstruct a PendingCommand from a SQLite row dict."""
        return PendingCommand(
            pending_id=row["pending_id"],
            command=Command(**json.loads(row["command_json"])),
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            decided_at=row["decided_at"],
            operator_id=row["operator_id"],
            reason=row["reason"],
        )
