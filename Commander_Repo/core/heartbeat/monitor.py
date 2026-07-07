"""monitor.py
HeartbeatMonitor - node failure detection system
- Track node heartbeat timestamps
- Detect node timeouts and declare nodes DEAD
- Fire on_node_failed / on_node_recovered callbacks
- Provide tick() for tests + threaded loop for production
"""

from __future__ import annotations

# Standard Library

import time
import threading
from typing import Any, Callable

# Third-Party Libraries

# Project Imports

from core.node_registry.registry import NodeRegistry
from core.utils.logger import log

# region  CLASS - HeartbeatMonitor

class HeartbeatMonitor:

    """
    Monitors node liveness via heartbeat timestamps.

    Detects timeouts and notifies via direct callbacks -
    no EventBus required. Provides tick() for deterministic
    test control and a background thread for production.
    """

    # region  INITIALISATION

    def __init__(
        self,
        timeout:           float                         = 10.0,
        registry:          NodeRegistry | None           = None,
        on_node_failed:    Callable[[dict[str, Any]], None] | None = None,
        on_node_recovered: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.timeout            = timeout
        self.registry           = registry
        self._on_node_failed    = on_node_failed    or (lambda p: None)  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        self._on_node_recovered = on_node_recovered or (lambda p: None)  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        self._last_seen:  dict[str, float] = {}
        self._dead_nodes: set[str]         = set()   # guards against duplicate NODE_FAILED
        self._running = False

    # endregion

    # region  PUBLIC API

    def update(self, node_id: str) -> None:
        """Record a live heartbeat for node_id."""
        now      = time.time()
        was_dead = node_id in self._dead_nodes

        self._last_seen[node_id] = now

        if self.registry:
            self.registry.heartbeat(node_id)

        # Fire NODE_RECOVERED only if the node was previously declared dead
        if was_dead:
            self._dead_nodes.discard(node_id)
            log("HeartbeatMonitor", f"recovered: {node_id}", channel="HEARTBEAT")
            self._on_node_recovered({  # pyright: ignore[reportUnknownMemberType]
                "node_id":   node_id,
                "timestamp": now,
                "source":    "heartbeat_monitor",
            })

        log("HeartbeatMonitor", f"heartbeat from {node_id}", channel="HEARTBEAT", level="TRACE")

    def start(self) -> None:
        """
        Start the background monitoring loop in a daemon thread.

        Seeds _last_seen for every node the registry reports as ACTIVE
        right now, giving every such node a fair, full timeout window
        to send a real heartbeat in THIS process's lifetime.
        """
        now = time.time()
        seeded = 0
        if self.registry:
            for node_id in self.registry.get_alive():
                if node_id not in self._last_seen:
                    self._last_seen[node_id] = now
                    seeded += 1
        if seeded:
            log("HeartbeatMonitor",
                f"seeded {seeded} hydrated ACTIVE node(s) with a fresh "
                f"{self.timeout}s grace window",
                channel="HEARTBEAT")

        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log("HeartbeatMonitor", f"started (timeout={self.timeout}s)", channel="HEARTBEAT")

    def stop(self) -> None:
        """Stop the background monitoring loop."""
        self._running = False

    def tick(self) -> None:
        """Single manual check - use in tests for deterministic control."""
        now = time.time()
        for node_id, last in list(self._last_seen.items()):
            if now - last > self.timeout:
                self._declare_dead(node_id, now)

    # endregion

    # region  PRIVATE METHODS

    def _loop(self) -> None:
        """Background thread body - calls tick() every second."""
        while self._running:
            self.tick()
            time.sleep(1)

    def _declare_dead(self, node_id: str, now: float) -> None:
        """Declare node_id dead: mark offline in registry, fire on_node_failed callback.

        Guarded by _dead_nodes set to prevent the same node triggering
        duplicate NODE_FAILED events before a heartbeat recovery.
        """
        if node_id in self._dead_nodes:
            return  # already declared dead - don't fire callback again

        log("HeartbeatMonitor", f"timeout - declaring dead: {node_id}", channel="HEARTBEAT")
        self._dead_nodes.add(node_id)
        del self._last_seen[node_id]

        if self.registry:
            self.registry.mark_dead(node_id)

        self._on_node_failed({  # pyright: ignore[reportUnknownMemberType]
            "node_id":   node_id,
            "reason":    "HEARTBEAT_TIMEOUT",
            "source":    "heartbeat_monitor",
            "timestamp": now,
        })

    # endregion

# endregion (end of class HeartbeatMonitor)
