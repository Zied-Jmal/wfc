"""registry.py
NodeRegistry - global node state store
- Register and track nodes
- Maintain heartbeat / status updates
- Query nodes by type, capability, status, and location
- Select closest node by Euclidean distance
"""

from __future__ import annotations

import contextlib
import math
import time
from collections.abc import Callable
from typing import Any

from core.persistence.database import Database
from core.utils.logger import log
from wfc_shared.enums.node_status import ACTIVE, OFFLINE, REGISTERED
from wfc_shared.schemas.nodes import NodeRecord

# region  HELPERS


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean distance between two (x, y) or (lat, lon) points.
    Swap with haversine for real geo coordinates - interface stays identical.
    """
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


# endregion

# region  CLASS - NodeRegistry


class NodeRegistry:
    """Central in-memory registry of all nodes in the system.
    Pure data store - no side effects, no event emission.
    """

    # region  INITIALISATION

    def __init__(
        self, db: Database | None = None, on_node_available: Callable[[str, list[str]], None] | None = None
    ) -> None:
        """Args:
        db: optional Database instance for persistence.
        on_node_available: optional callable(node_id, capabilities)
        Called when a node first becomes ACTIVE (REGISTEREDACTIVE
        or OFFLINEACTIVE). Used by CommanderCore to write a
        NODE_BECAME_AVAILABLE domain event.
        """
        self._nodes: dict[str, NodeRecord] = {}
        self._repo = None
        self._on_node_available = on_node_available
        if db is not None:
            from core.persistence.repositories.node_repo import NodeRepository

            self._repo = NodeRepository(db)
            for record in self._repo.get_all():
                self._nodes[record.node_id] = record
            if self._nodes:
                log(
                    "NodeRegistry",
                    f"hydrated {len(self._nodes)} node(s) from database",
                    channel="REGISTRY",
                )

    # endregion

    # region  REGISTRATION

    def register(
        self,
        node_id: str,
        node_type: str = "UNKNOWN",
        capabilities: list[str] | None = None,
        zone: str | None = None,
        location: tuple[float, float] | None = None,
    ) -> None:
        """Register a node. Sets status to REGISTERED (not ACTIVE).
        ACTIVE is granted separately by grant_active() after first heartbeat.
        Re-registration updates capabilities/zone/location.

        If the existing record's status was OFFLINE, this re-registration
        is a genuine recovery. Any current_job it held is now stale -
        cleared automatically on re-registration from OFFLINE state.
        """
        if node_id in self._nodes:
            existing = self._nodes[node_id]
            was_offline = existing.status == OFFLINE

            updates: dict[str, Any] = {}
            if capabilities is not None and capabilities != existing.capabilities:
                updates["capabilities"] = capabilities
            if zone is not None:
                updates["zone"] = zone
            if location is not None:
                updates["location"] = location
            if was_offline and existing.current_job is not None:
                updates["current_job"] = None
                log(
                    "NodeRegistry",
                    f"{node_id} re-registered after OFFLINE - clearing stale current_job={existing.current_job}",
                    channel="REGISTRY",
                )
            if updates:
                self._nodes[node_id] = existing.model_copy(update=updates)
                if self._repo is not None:
                    self._repo.upsert(self._nodes[node_id])
            return

        record = NodeRecord(
            node_id=node_id,
            node_type=node_type,
            capabilities=capabilities or [],
            status=REGISTERED,  # R2: REGISTERED, not ACTIVE
            zone=zone,
            location=location,
        )
        self._nodes[node_id] = record
        if self._repo is not None:
            self._repo.upsert(record)
        log(
            "NodeRegistry",
            f"REGISTERED {node_id} ({node_type}) caps={record.capabilities} zone={zone} location={location}",
            channel="REGISTRY",
        )

    def grant_active(self, node_id: str) -> None:
        """System grants ACTIVE status after first valid heartbeat.
        Only ACTIVE nodes receive commands from the rule engine.
        Called by heartbeat() on first heartbeat from a REGISTERED node.
        """
        if node_id not in self._nodes:
            return  # R1: unknown node - ignored
        rec = self._nodes[node_id]
        if rec.status == REGISTERED:
            self._nodes[node_id] = rec.model_copy(update={"status": ACTIVE})
            log("NodeRegistry", f"ACTIVE granted: {node_id} (first heartbeat validated)", channel="REGISTRY")
            if self._on_node_available is not None:  # pyright: ignore[reportUnknownMemberType]
                with contextlib.suppress(Exception):
                    self._on_node_available(node_id, rec.capabilities)  # pyright: ignore[reportUnknownMemberType]

    # endregion

    # region  HEARTBEAT / STATUS
    def heartbeat(self, node_id: str) -> None:
        """Heartbeat updates liveness timestamp ONLY.
        Does NOT change mission state, fire state, or any other state.
        Ignored if node is UNREGISTERED (not in _nodes).

        Register()'s re-registration branch clears a stale current_job
        when a node recovers from OFFLINE. This method's own
        OFFLINE -> ACTIVE transition does NOT do the same - heartbeat()
        only ever touches liveness/status, never job assignment.
        """
        log("REGISTRY", f"heartbeat received from {node_id}", channel="DEBUG")

        if node_id not in self._nodes:
            return  # R1: heartbeat from unregistered node - silently ignored

        rec = self._nodes[node_id]
        now = time.time()
        updates: dict[str, Any] = {"last_seen": now}

        became_active = False
        if rec.status == REGISTERED:
            updates["status"] = ACTIVE
            became_active = True
            log("NodeRegistry", f"ACTIVE granted: {node_id} (first heartbeat validated)", channel="REGISTRY")
        elif rec.status == OFFLINE:
            updates["status"] = ACTIVE
            became_active = True
            log("NodeRegistry", f"ACTIVE recovered: {node_id} (came back from OFFLINE)", channel="REGISTRY")

        self._nodes[node_id] = rec.model_copy(update=updates)

        if self._repo is not None:
            self._repo.upsert(self._nodes[node_id])
        # ----------

        if became_active and self._on_node_available is not None:  # pyright: ignore[reportUnknownMemberType]
            with contextlib.suppress(Exception):
                self._on_node_available(node_id, rec.capabilities)  # pyright: ignore[reportUnknownMemberType]

    def mark_offline(self, node_id: str) -> None:
        """OFFLINE is ONLY set by the system (this method).
        Nodes never declare themselves dead.
        Called by HeartbeatMonitor on timeout or LWT via RegistryBridge.
        """
        if node_id in self._nodes:
            rec = self._nodes[node_id]
            updated = rec.model_copy(update={"status": OFFLINE})
            self._nodes[node_id] = updated
            if self._repo is not None:
                self._repo.upsert(updated)
            log("NodeRegistry", f"OFFLINE: {node_id}", channel="REGISTRY")

    def mark_dead(self, node_id: str) -> None:
        """Back-compat alias for mark_offline(). Kept for HeartbeatMonitor call sites."""
        self.mark_offline(node_id)

    # endregion

    # region  job tracking

    def assign_job(self, node_id: str, fire_id: str) -> None:
        """Mark node as busy with fire_id. Persisted to SQLite."""
        if node_id in self._nodes:
            self._nodes[node_id] = self._nodes[node_id].model_copy(update={"current_job": fire_id})
            if self._repo is not None:
                self._repo.upsert(self._nodes[node_id])
            log("NodeRegistry", f"job assigned: fire={fire_id[:8]} → {node_id}", channel="REGISTRY")

    def release_job(self, node_id: str) -> None:
        """Clear node's current_job, making it available for the next dispatch."""
        if node_id in self._nodes:
            self._nodes[node_id] = self._nodes[node_id].model_copy(update={"current_job": None})
            if self._repo is not None:
                self._repo.upsert(self._nodes[node_id])
            log("NodeRegistry", f"job released: {node_id}", channel="REGISTRY")

    # region  QUERIES - single node

    def exists(self, node_id: str) -> bool:
        """Return True if node_id is known to the registry (any status)."""
        return node_id in self._nodes

    def get(self, node_id: str) -> NodeRecord | None:
        """Return the NodeRecord for node_id, or None if not registered."""
        return self._nodes.get(node_id)

    def get_status(self, node_id: str) -> str | None:
        """Return the current status string for node_id, or None if unknown."""
        rec = self._nodes.get(node_id)
        return rec.status if rec else None

    # endregion

    # region  QUERIES - collections

    def get_all(self) -> dict[str, NodeRecord]:
        """Return a snapshot copy of all registered nodes (all statuses).

        NodeRecord is frozen (Pydantic ConfigDict frozen=True), so
        any attempted mutation raises an error at the call site instead
        of silently corrupting state. The dict copy is still returned
        so callers iterating over nodes don't see concurrent inserts.
        """
        return {nid: rec for nid, rec in self._nodes.items()}

    def get_alive(self) -> list[str]:
        """Return node IDs of all ACTIVE nodes.

        Returns:
            List of node_id strings for nodes with ACTIVE status.
        """
        return [nid for nid, rec in self._nodes.items() if rec.status == ACTIVE]

    def get_dead(self) -> list[str]:
        """Return node IDs of all OFFLINE nodes.

        Returns:
            List of node_id strings for nodes with OFFLINE status.
        """
        return [nid for nid, rec in self._nodes.items() if rec.status == OFFLINE]

    def get_by_type(self, node_type: str) -> list[str]:
        """Return node IDs of all ACTIVE nodes with the given type.

        Args:
            node_type: The node type string to filter by (e.g. "SWARM_LEADER").

        Returns:
            List of matching node_id strings.
        """
        return [nid for nid, rec in self._nodes.items() if rec.node_type == node_type and rec.status == ACTIVE]

    def get_by_capability(self, capability: str) -> list[str]:
        """Return node IDs of all ACTIVE nodes that have the given capability.

        Args:
            capability: The capability string to filter by (e.g. "SWARM_LEAD").

        Returns:
            List of matching node_id strings.
        """
        return [nid for nid, rec in self._nodes.items() if capability in rec.capabilities and rec.status == ACTIVE]

    def get_available(self, capability: str) -> list[str]:
        """Return ACTIVE nodes with the given capability that have no active job.

        Use this instead of get_by_capability() when dispatching commands
        to prevent double-dispatch to an already-busy node.

        Args:
            capability: The capability string to filter by (e.g. "SWARM_LEAD").

        Returns:
            List of idle node_id strings matching the capability.
        """
        available = []
        for nid, rec in self._nodes.items():
            if capability in rec.capabilities and rec.status == ACTIVE and rec.current_job is None:
                available.append(nid)  # pyright: ignore[reportUnknownMemberType]
            else:
                log(
                    "REGISTRY",
                    f"get_available: {nid} caps={rec.capabilities} status={rec.status} job={rec.current_job}",
                    channel="DEBUG",
                )
        log("REGISTRY", f"get_available({capability}) -> {available}", channel="DEBUG")
        return available  # pyright: ignore[reportUnknownVariableType]

    # endregion

    # region  QUERIES - proximity

    def get_closest(
        self,
        capability: str,
        location: tuple[float, float],
        idle_only: bool = True,
    ) -> str | None:
        """Return closest ACTIVE node with capability to the given location.

        Args:
            capability: The capability string to filter by.
            location: (lat_deg, lon_deg) WGS-84 coordinates to measure distance from.
            idle_only: If True (default), skip nodes with an active job.

        Returns:
            node_id of the closest matching node, or None if no candidates.
        """
        candidates = [
            rec
            for rec in self._nodes.values()
            if capability in rec.capabilities
            and rec.status == ACTIVE
            and rec.location is not None
            and (not idle_only or rec.current_job is None)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: _distance(r.location, location)).node_id  # pyright: ignore[reportArgumentType]

    def get_in_zone(
        self,
        capability: str,
        zone: str,
        idle_only: bool = True,
    ) -> list[str]:
        """Return ACTIVE nodes with capability in the given zone.

        Args:
            capability: The capability string to filter by.
            zone: Zone label to filter by (e.g. "zone_alpha").
            idle_only: If True (default), skip nodes with an active job.

        Returns:
            List of matching node_id strings.
        """
        return [
            nid
            for nid, rec in self._nodes.items()
            if capability in rec.capabilities
            and rec.status == ACTIVE
            and rec.zone == zone
            and (not idle_only or rec.current_job is None)
        ]

    # endregion


# endregion (end of class NodeRegistry)
