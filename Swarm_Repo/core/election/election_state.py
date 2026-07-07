# core/election/election_state.py
# Tracks bully election term and state per node.
from __future__ import annotations

import threading
import time


from core.utils.logger import log


class ElectionState:
    """Tracks the current election term and state for a backup leader node.

    Rules:
      - incoming_term > current_term -> accept new term, reset state.
      - incoming_term == current_term -> process normally.
      - incoming_term < current_term -> ignore (stale message).
    """

    def __init__(self, initial_term: int = 1) -> None:
        self._lock = threading.Lock()
        self._term = initial_term
        self._in_election = False
        self._received_ok = False
        self._won = False
        self._election_start_time: float | None = None

# PUBLIC API

    @property
    def term(self) -> int:
        """The current election term number."""
        return self._term

    @property
    def in_election(self) -> bool:
        """Whether an election is currently in progress."""
        return self._in_election

    @property
    def received_ok(self) -> bool:
        """Whether at least one ELECTION_OK was received this term."""
        return self._received_ok

    @property
    def won(self) -> bool:
        """Whether this node has won the current election."""
        return self._won

    def accept_term(self, incoming_term: int) -> bool:
        """Try to accept *incoming_term*.

        Returns:
            True if accepted (incoming >= current), False if stale.

        Resets election state when a new term is accepted.
        """
        with self._lock:
            if incoming_term < self._term:
                return False
            if incoming_term > self._term:
                log(
                    "ElectionState",
                    f"new term {incoming_term} (was {self._term}) -- reset",
                    channel="SYSTEM",
                )
                self._term = incoming_term
                self._in_election = False
                self._received_ok = False
                self._won = False
                self._election_start_time = None
            return True

    def start_election(self) -> int:
        """Start a new election -- bump term and mark in-progress.

        Returns:
            The new term number.
        """
        with self._lock:
            self._term += 1
            self._in_election = True
            self._received_ok = False
            self._won = False
            self._election_start_time = time.time()
            log("ElectionState", f"election started term={self._term}", channel="SYSTEM")
            return self._term

    def mark_ok_received(self) -> None:
        """Record that an ELECTION_OK was received this term."""
        with self._lock:
            self._received_ok = True

    def mark_won(self) -> None:
        """Mark the current election as won by this node."""
        with self._lock:
            self._won = True
            self._in_election = False
            log("ElectionState", f"election WON term={self._term}", channel="SYSTEM")

    def mark_lost(self) -> None:
        """Mark the current election as lost or deferred."""
        with self._lock:
            self._in_election = False
            log(
                "ElectionState",
                f"election LOST/deferred term={self._term}",
                channel="SYSTEM",
            )

    def elapsed(self) -> float:
        """Seconds since election was started, or 0.0 if not in election."""
        if self._election_start_time is None:
            return 0.0
        return time.time() - self._election_start_time
