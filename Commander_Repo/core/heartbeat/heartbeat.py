"""heartbeat.py
Heartbeat - periodic heartbeat publisher
- Execute heartbeat callback periodically
- Manage heartbeat thread lifecycle
- Support configurable heartbeat interval
"""

from __future__ import annotations

import threading

# Standard Library
import time
from typing import Any

# Third-Party Libraries
# Project Imports
from core.utils.logger import log

# region  CLASS - Heartbeat


class Heartbeat:
    """
    Periodically executes a heartbeat callback in a background thread.
    """

    # region  INITIALISATION

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._running = False
        self._thread: threading.Thread | None = None
        self._callback: Any = None

    # endregion

    # region  PUBLIC API

    def start(self, callback: Any) -> None:
        """Start the heartbeat background thread.

        Args:
            callback: zero-argument callable invoked every `interval` seconds.
        """
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log("Heartbeat", f"started (interval={self.interval}s)", channel="HEARTBEAT")

    def stop(self) -> None:
        """Signal the background thread to stop after its current sleep."""
        self._running = False
        log("Heartbeat", "stopped", channel="HEARTBEAT")

    # endregion

    # region  PRIVATE METHODS

    def _loop(self) -> None:
        """Background thread body - calls callback every interval seconds."""
        while self._running:
            if self._callback:
                try:
                    self._callback()
                except Exception as exc:
                    log("Heartbeat", f"callback error: {exc}", channel="HEARTBEAT")
            time.sleep(self.interval)

    # endregion


# endregion (end of class Heartbeat)
