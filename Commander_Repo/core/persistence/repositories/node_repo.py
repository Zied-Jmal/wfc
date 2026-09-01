"""node_repo.py
NodeRepository - persistence for NodeRecord rows
- Upsert / load / delete NodeRecord rows
- Translate between NodeRecord (pydantic) and SQL rows
"""

from __future__ import annotations

import json
from typing import Any

from core.persistence.database import Database
from wfc_shared.schemas.nodes import NodeRecord


class NodeRepository:
    """Persistence layer for NodeRecord rows.

    One row per node_id (snapshot model). Re-registrations, heartbeats,
    and status changes all call upsert() - the row is always current truth.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, record: NodeRecord) -> None:
        """Insert or overwrite the row for record.node_id.

        The updated_at column was removed from the upsert because it
        was written on every heartbeat but never read by any query,
        rule, or snapshot merge - pure write-overhead with no consumer.
        """
        loc = record.location if record.location else (None, None)
        self._db.execute(  # pyright: ignore[reportUnknownMemberType]
            """
            INSERT INTO nodes (
                node_id, node_type, capabilities, status,
                last_seen, registered_at, zone,
                location_x, location_y, current_job
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                node_type     = excluded.node_type,
                capabilities  = excluded.capabilities,
                status        = excluded.status,
                last_seen     = excluded.last_seen,
                zone          = excluded.zone,
                location_x    = excluded.location_x,
                location_y    = excluded.location_y,
                current_job   = excluded.current_job
            """,
            (
                record.node_id,
                record.node_type,
                json.dumps(record.capabilities),
                record.status,
                record.last_seen,
                record.registered_at,
                record.zone,
                loc[0] if loc[0] is not None else None,
                loc[1] if loc[1] is not None else None,
                record.current_job,
            ),
        )

    def delete(self, node_id: str) -> None:
        """Remove a node row - used when decommissioning a node.

        Note: the node will re-register on restart, so this is only
        needed for permanent decommissioning, not normal OFFLINE events.
        """
        self._db.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))  # pyright: ignore[reportUnknownMemberType]

    def get(self, node_id: str) -> NodeRecord | None:
        """Return the NodeRecord for node_id, or None if not persisted."""
        row = self._db.query_one("SELECT * FROM nodes WHERE node_id = ?", (node_id,))  # pyright: ignore[reportUnknownMemberType]
        return self._row_to_record(row) if row else None

    def get_all(self) -> list[NodeRecord]:
        """Load all persisted node records - called on startup to hydrate NodeRegistry."""
        return [self._row_to_record(r) for r in self._db.query("SELECT * FROM nodes")]  # pyright: ignore[reportUnknownMemberType]

    @staticmethod
    def _row_to_record(row: Any) -> NodeRecord:
        """Reconstruct a NodeRecord from a SQLite row dict."""
        keys = row.keys()
        data: dict[str, Any] = {
            "node_id": row["node_id"],
            "node_type": row["node_type"],
            "capabilities": json.loads(row["capabilities"]),
            "status": row["status"],
            "last_seen": row["last_seen"],
            "registered_at": row["registered_at"],
        }
        if "zone" in keys and row["zone"] is not None:
            data["zone"] = row["zone"]
        if row["location_x"] is not None and row["location_y"] is not None:
            data["location"] = (row["location_x"], row["location_y"])
        if "current_job" in keys and row["current_job"] is not None:
            data["current_job"] = row["current_job"]
        return NodeRecord(**data)
