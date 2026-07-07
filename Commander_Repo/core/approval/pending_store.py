"""pending_store.py
PendingCommandStore - approval queue for commands
- Hold commands pending human approval
- Enforce TTL expiry on pending commands
- Dispatch approved commands via CommandDispatcher
- Publish status events (PENDING/APPROVED/REJECTED)
directly over MQTT (no EventBus)
"""

from __future__ import annotations

import time

from typing import Final

from wfc_shared.schemas.pending import PendingCommand
from wfc_shared.enums.approval import PENDING, APPROVED, REJECTED
from wfc_shared.enums.topics import APPROVAL_PENDING
from wfc_shared.schemas.domain_event import DomainEvent
from wfc_shared.enums.domain_event_types import ESCALATION_APPROVED, ESCALATION_REJECTED
from core.commands_monitor.command_dispatcher import CommandDispatcher
from core.messaging.mqtt_client import MQTTClient
from core.state.domain_event_log import DomainEventLog
from core.persistence.database import Database
from core.utils.logger import log

DEFAULT_TTL: Final = 30.0

# region  CLASS - PendingCommandStore

class PendingCommandStore:

    """
    In-memory store for commands awaiting human approval.
    Publishes status changes directly to MQTT - no EventBus needed.
    """

    # region  INITIALISATION

    def __init__(self, dispatcher: CommandDispatcher, mqtt: MQTTClient, node_id: str, ttl: float = DEFAULT_TTL, db: Database | None = None, event_log: DomainEventLog | None = None) -> None:
        """
        Args:
            db:        optional Database instance for persistence.
            event_log: optional DomainEventLog. When provided, writes
                       ESCALATION_APPROVED / ESCALATION_REJECTED events
                       on operator decisions.
        """
        self._dispatcher = dispatcher
        self._mqtt       = mqtt
        self._node_id    = node_id
        self._ttl        = ttl
        self._event_log  = event_log
        self._store: dict[str, PendingCommand] = {}
        self._repo = None
        if db is not None:
            from core.persistence.repositories.pending_repo import PendingRepository
            self._repo = PendingRepository(db)
            for pending in self._repo.get_all():
                self._store[pending.pending_id] = pending
            if self._store:
                log(
                    "PendingStore",
                    f"hydrated {len(self._store)} pending command(s) from database",
                    channel="APPROVAL",
                )

    # endregion

    # region  PUBLIC API - write

    def add(self, pending: PendingCommand) -> None:
        """Add a command to the pending store and publish COMMAND_PENDING over MQTT."""
        if self._ttl is not None and pending.expires_at is None:  # pyright: ignore[reportUnnecessaryComparison]
            pending = pending.model_copy(
                update={"expires_at": pending.created_at + self._ttl}
            )
        self._store[pending.pending_id] = pending
        if self._repo is not None:
            self._repo.upsert(pending)
        self._mqtt.publish(APPROVAL_PENDING, {
            "event":        "COMMAND_PENDING",
            "pending_id":   pending.pending_id,
            "command_type": pending.command.command_type,
            "target_node":  pending.command.target_node,
            "payload":      pending.command.payload,
            "created_at":   pending.created_at,
            "expires_at":   pending.expires_at,
            "source":       self._node_id,
        }, qos=1)
        log("PendingStore",
            f"held pending_id={pending.pending_id[:8]} "
            f"cmd={pending.command.command_type}",
            channel="APPROVAL")

    def approve(self, pending_id: str, operator_id: str | None = None) -> None:
        """Approve a pending command - dispatches it immediately.
        Dispatches first, only persists APPROVED after the send succeeds.
        """
        pending = self._store.get(pending_id)
        if pending is None or pending.status != PENDING:
            return
        now     = time.time()
        pending = pending.model_copy(update={
            "status":      APPROVED,
            "decided_at":  now,
            "operator_id": operator_id,
        })
        # Dispatch first - only persist if send succeeds
        trace_id = self._dispatcher.send(pending.command)
        self._store[pending_id] = pending
        if self._repo is not None:
            self._repo.upsert(pending)

        # write ESCALATION_APPROVED domain event for audit trail
        if self._event_log is not None:  # pyright: ignore[reportUnknownMemberType]
            fire_id = getattr(pending.command, "payload", {}).get("fire_id")
            self._event_log.append(DomainEvent(  # pyright: ignore[reportUnknownMemberType]
                event_type=ESCALATION_APPROVED,  # pyright: ignore[reportArgumentType]
                fire_id=fire_id,
                reason=f"approved_by_{operator_id or 'operator'}",
                payload={"pending_id": pending_id, "trace_id": trace_id},
            ))

        self._mqtt.publish(APPROVAL_PENDING, {
            "event":       "COMMAND_APPROVED",
            "pending_id":  pending_id,
            "trace_id":    trace_id,
            "operator_id": operator_id,
            "decided_at":  now,
            "source":      self._node_id,
        }, qos=1)
        log("PendingStore",
            f"approved pending_id={pending_id[:8]} trace={trace_id[:8]}",
            channel="APPROVAL")

    def reject(
        self,
        pending_id:  str,
        reason:      str      = "operator_rejected",
        operator_id: str | None = None,
    ) -> None:
        """Reject a pending command."""
        pending = self._store.get(pending_id)
        if pending is None or pending.status != PENDING:
            return
        now     = time.time()
        pending = pending.model_copy(update={
            "status":      REJECTED,
            "decided_at":  now,
            "operator_id": operator_id,
            "reason":      reason,
        })
        self._store[pending_id] = pending
        if self._repo is not None:
            self._repo.upsert(pending)

        # write ESCALATION_REJECTED domain event for audit trail
        if self._event_log is not None:  # pyright: ignore[reportUnknownMemberType]
            fire_id = getattr(pending.command, "payload", {}).get("fire_id")
            self._event_log.append(DomainEvent(  # pyright: ignore[reportUnknownMemberType]
                event_type=ESCALATION_REJECTED,  # pyright: ignore[reportArgumentType]
                fire_id=fire_id,
                reason=reason,
                payload={"pending_id": pending_id, "operator_id": operator_id},
            ))

        self._mqtt.publish(APPROVAL_PENDING, {
            "event":       "COMMAND_REJECTED",
            "pending_id":  pending_id,
            "reason":      reason,
            "operator_id": operator_id,
            "decided_at":  now,
            "source":      self._node_id,
        }, qos=1)
        log("PendingStore",
            f"rejected pending_id={pending_id[:8]} reason={reason}",
            channel="APPROVAL")

    def expire_stale(self) -> list[str]:
        """Expire all PENDING commands past their TTL.

        Publishes COMMAND_EXPIRED (not COMMAND_REJECTED) so the dashboard
        can distinguish TTL expiry from an operator decision.
        Returns list of expired pending_ids.
        """
        now     = time.time()
        expired = [
            pid for pid, p in self._store.items()
            if p.status == PENDING
            and p.expires_at is not None
            and now >= p.expires_at
        ]
        for pid in expired:
            pending = self._store.get(pid)
            if pending is None:
                continue
            pending = pending.model_copy(update={
                "status":     "EXPIRED",
                "decided_at": now,
                "reason":     "ttl_expired",
            })
            self._store[pid] = pending
            if self._repo is not None:
                self._repo.upsert(pending)
            self._mqtt.publish(APPROVAL_PENDING, {
                "event":      "COMMAND_EXPIRED",   # distinct from COMMAND_REJECTED
                "pending_id": pid,
                "reason":     "ttl_expired",
                "decided_at": now,
                "source":     self._node_id,
            }, qos=1)
            log("PendingStore",
                f"expired pending_id={pid[:8]} (TTL)",
                channel="APPROVAL")
        return expired

    # endregion

    # region  PUBLIC API - read

    def get(self, pending_id: str) -> PendingCommand | None:
        return self._store.get(pending_id)

    def get_all_pending(self) -> list[PendingCommand]:
        return [p for p in self._store.values() if p.status == PENDING]

    # endregion

# endregion (end of class PendingCommandStore)
