# core/state/domain_event_log.py
# decisions.  This is the "events for truth" half of
# the hybrid architecture.  State stores (FireStateStore,
# MissionStore) remain the "state for speed" half.
from __future__ import annotations

from core.persistence.repositories.domain_event_repo import DomainEventRepository
from core.persistence.database import Database
from wfc_shared.schemas.domain_event import DomainEvent

class DomainEventLog:
    """Append-only log of all commander domain events.

    Single writer: the active commander only.
    The backup receives events via snapshot sync and replays them
    through append() - INSERT OR IGNORE makes that idempotent.

    This is the source of truth for:
      - Idempotency: "has ESCALATION_REQUESTED already been written
        for fire X?"
      - Context recovery: "what was the last thing that happened to
        fire X?" - used by redispatch_fire() to distinguish a fresh
        dispatch from a re-dispatch.
      - Audit trail: full queryable history replacing fire.history list.
      - State reconstruction: replay all events on startup (Step 4).
    """

    def __init__(self, db: Database)  -> None:
        self._repo = DomainEventRepository(db)

# WRITE

    def append(self, event: DomainEvent) -> DomainEvent:
        """Write an event to the log.  Returns the event with .sequence set.

        Safe to call with a duplicate event_id (INSERT OR IGNORE).
        """
        return self._repo.insert(event)

# READ - per-fire

    def get_for_fire(self, fire_id: str) -> list[DomainEvent]:
        """All events for a fire, oldest-first."""
        return self._repo.get_by_fire_id(fire_id)

    def get_last_for_fire(
        self, fire_id: str, event_type: str | None = None
    ) -> DomainEvent | None:
        """Last event for a fire, optionally filtered by type."""
        return self._repo.get_last(fire_id, event_type)

    def has_event(self, fire_id: str, event_type: str) -> bool:
        """
        Idempotency check: has this event type already been written
        for this fire? Used by HighSeverityRule to prevent duplicate
        ESCALATION_REQUESTED entries.
        """
        return self._repo.exists(fire_id, event_type)

    def has_event_id(self, event_id: str) -> bool:
        """Deduplication check used during snapshot sync."""
        return self._repo.exists_by_id(event_id)

    # READ - bulk

    def get_recent(self, limit: int = 200) -> list[DomainEvent]:
        """Last N events across all fires - for startup replay and dashboard."""
        return self._repo.get_recent(limit)

    def get_since(self, since_timestamp: float) -> list[DomainEvent]:
        """Events written after since_timestamp - for snapshot sync (Step 5)."""
        return self._repo.get_since(since_timestamp)
