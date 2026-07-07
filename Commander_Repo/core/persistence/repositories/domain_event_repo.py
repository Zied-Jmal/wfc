# core/persistence/repositories/domain_event_repo.py
# F9 fix: uses cur.rowcount instead of cur.lastrowid
# to detect ignored duplicates.
from __future__ import annotations
from typing import Any

import json

from wfc_shared.schemas.domain_event import DomainEvent

class DomainEventRepository:
    """Thin data-access object for the domain_events table.

    All writes are append-only - no UPDATE or DELETE.
    The integer primary key `id` is the sequence number that
    DomainEventLog exposes as DomainEvent.sequence.
    """

    def __init__(self, db: Any)  -> None:
        self._db = db

# WRITE

    def insert(self, event: DomainEvent) -> DomainEvent:
        """Persist event and return it with .sequence set.
        Uses INSERT OR IGNORE so replaying the same event_id is safe.
        Uses cur.rowcount == 1 to detect success. rowcount is the
        definitive affected-rows count; lastrowid can be 0 on
        duplicates but rowcount explicitly tells us if the row was
        ignored or inserted.
        """
        cur = self._db.execute(
            """
            INSERT OR IGNORE INTO domain_events
                (event_id, event_type, fire_id, node_id, reason,
                 payload_json, timestamp, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.fire_id,
                event.node_id,
                event.reason,
                json.dumps(event.payload),
                event.timestamp,
                event.source,
            ),
        )

# rowcount == 1 means new row inserted; 0 means duplicate ignored.
        if cur.rowcount == 1:
            return event.model_copy(update={"sequence": cur.lastrowid})

# Duplicate: fetch the existing sequence from the database.
        row = self._db.query_one(
            "SELECT id FROM domain_events WHERE event_id = ?",
            (event.event_id,),
        )
        seq = row["id"] if row else None
        return event.model_copy(update={"sequence": seq})

# READ

    def _row_to_event(self, row: Any) -> DomainEvent:
        return DomainEvent(
            event_id   = row["event_id"],
            event_type = row["event_type"],
            fire_id    = row["fire_id"],
            node_id    = row["node_id"],
            reason     = row["reason"],
            payload    = json.loads(row["payload_json"] or "{}"),
            sequence   = row["id"],
            timestamp  = row["timestamp"],
            source     = row["source"],
        )

    def get_by_fire_id(self, fire_id: str) -> list[DomainEvent]:
        rows = self._db.query(
            "SELECT * FROM domain_events WHERE fire_id = ? ORDER BY id ASC",
            (fire_id,),
        )
        return [self._row_to_event(r) for r in rows]

    def get_last(
        self, fire_id: str, event_type: str | None = None
    ) -> DomainEvent | None:
        if event_type:
            row = self._db.query_one(
                """SELECT * FROM domain_events
                   WHERE fire_id = ? AND event_type = ?
                   ORDER BY id DESC LIMIT 1""",
                (fire_id, event_type),
            )
        else:
            row = self._db.query_one(
                "SELECT * FROM domain_events WHERE fire_id = ? ORDER BY id DESC LIMIT 1",
                (fire_id,),
            )
        return self._row_to_event(row) if row else None

    def exists(self, fire_id: str, event_type: str) -> bool:
        row = self._db.query_one(
            "SELECT 1 FROM domain_events WHERE fire_id = ? AND event_type = ? LIMIT 1",
            (fire_id, event_type),
        )
        return row is not None

    def exists_by_id(self, event_id: str) -> bool:
        row = self._db.query_one(
            "SELECT 1 FROM domain_events WHERE event_id = ? LIMIT 1",
            (event_id,),
        )
        return row is not None

    def get_recent(self, limit: int = 200) -> list[DomainEvent]:
        rows = self._db.query(
            "SELECT * FROM domain_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_event(r) for r in reversed(rows)]

    def get_since(self, since_timestamp: float) -> list[DomainEvent]:
        rows = self._db.query(
            "SELECT * FROM domain_events WHERE timestamp > ? ORDER BY id ASC",
            (since_timestamp,),
        )
        return [self._row_to_event(r) for r in rows]
