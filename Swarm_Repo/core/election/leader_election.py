# core/election/leader_election.py
"""Bully algorithm summary:

    1. Node detects leader heartbeat timeout.
    2. Node sends ELECTION_START to all higher-ID peers.
    3. If any higher peer replies ELECTION_OK within timeout,
       this node steps back (mark_lost) -- a higher node wins.
    4. If no ELECTION_OK received within timeout, this node
       declares victory, sends ELECTION_WIN to all peers,
       then calls on_win().
    5. A higher-ID node that receives ELECTION_START sends
       ELECTION_OK and starts its own election.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from core.election.election_state import ElectionState
from core.messaging.mqtt_client import MQTTClient
from core.utils.logger import log
from wfc_shared.enums.topics import swarm_internal_topic


class LeaderElection:
    """Drives the bully election protocol over MQTT internal topics.

    The owning node (SwarmLeaderNode) must:
      1. Call start_election() when the current leader is declared dead.
      2. Call on_election_message() for every
         wfc/swarm/internal/{self.node_id} message.
      3. Provide an mqtt object with .publish() and .subscribe().
    """

    def __init__(
        self,
        node_id: str,
        zone: str,
        peer_ids: list[str],
        state: ElectionState,
        mqtt: MQTTClient,
        on_win: Callable[[], None],
        on_lost: Callable[[], None],
        timeout: float = 5.0,
    ) -> None:
        """Initialise the leader election engine.

        Args:
            node_id: This node's identifier.
            zone: The zone this node belongs to.
            peer_ids: All **other** backup peer node IDs.
            state: Shared election state instance.
            mqtt: MQTT client used for publishing messages.
            on_win: Called when this node wins an election.
            on_lost: Called when a higher node wins the election.
            timeout: Seconds to wait for ELECTION_OK before declaring victory.
        """
        self._node_id = node_id
        self._zone = zone
        self._peers = peer_ids
        self._state = state
        self._mqtt = mqtt
        self._on_win = on_win
        self._on_lost = on_lost
        self._timeout = timeout
        self._timer: threading.Timer | None = None

# PUBLIC API

    def start_election(self) -> None:
        """Initiate a bully election from this node."""
        term = self._state.start_election()

        higher_peers = [p for p in self._peers if p > self._node_id]

        if not higher_peers:
            log(
                "LeaderElection",
                f"{self._node_id} has no higher peers -- declaring victory (term={term})",
                channel="SYSTEM",
            )
            self._declare_victory(term)
            return

        for peer_id in higher_peers:
            self._send(
                peer_id,
                {
                    "type": "ELECTION_START",
                    "from_node_id": self._node_id,
                    "zone": self._zone,
                    "term": term,
                    "timestamp": time.time(),
                },
            )

        self._reset_timer(term)

    def on_election_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming election protocol message.

        Called by SwarmLeaderNode when a message arrives on
        wfc/swarm/internal/{self.node_id}.

        Args:
            payload: The parsed MQTT message payload dictionary.
        """
        msg_type: str | None = payload.get("type")  # pyright: ignore[reportUnknownMemberType]
        term: int | None = payload.get("term", 1)  # pyright: ignore[reportUnknownMemberType]

        if not self._state.accept_term(term):  # pyright: ignore[reportArgumentType]
            log(
                "LeaderElection",
                f"stale election message term={term} (current={self._state.term}) -- ignored",
                channel="SYSTEM",
            )
            return

        if msg_type == "ELECTION_START":
            self._handle_election_start(payload)
        elif msg_type == "ELECTION_OK":
            self._handle_election_ok(payload)
        elif msg_type == "ELECTION_WIN":
            self._handle_election_win(payload)
        else:
            log("LeaderElection", f"unknown election msg type: {msg_type}", channel="SYSTEM")

# PRIVATE -- message handlers

    def _handle_election_start(self, payload: dict[str, Any]) -> None:
        """Respond to ELECTION_START from a lower-ID peer."""
        from_node: str = payload.get("from_node_id", "")  # pyright: ignore[reportUnknownMemberType]
        term: int = payload.get("term", 1)  # pyright: ignore[reportUnknownMemberType]

        if from_node >= self._node_id:
            return

        self._send(
            from_node,
            {
                "type": "ELECTION_OK",
                "from_node_id": self._node_id,
                "term": term,
                "timestamp": time.time(),
            },
        )
        log(
            "LeaderElection",
            f"sent ELECTION_OK to {from_node} (term={term})",
            channel="SYSTEM",
        )

        if not self._state.in_election:
            self.start_election()

    def _handle_election_ok(self, payload: dict[str, Any]) -> None:
        """A higher node has responded -- we step back."""
        self._state.mark_ok_received()
        self._cancel_timer()
        self._state.mark_lost()
        self._on_lost()
        log(
            "LeaderElection",
            f"ELECTION_OK received -- stepping back (term={self._state.term})",
            channel="SYSTEM",
        )

    def _handle_election_win(self, payload: dict[str, Any]) -> None:
        """Another node has declared victory."""
        winner: str = payload.get("winner_id", "")  # pyright: ignore[reportUnknownMemberType]
        term: int = payload.get("term", 1)  # pyright: ignore[reportUnknownMemberType]
        self._cancel_timer()
        self._state.mark_lost()
        log(
            "LeaderElection",
            f"ELECTION_WIN: {winner} won term={term}",
            channel="SYSTEM",
        )
        self._on_lost()

# PRIVATE -- election outcome

    def _declare_victory(self, term: int) -> None:
        """Broadcast ELECTION_WIN and invoke the win callback."""
        self._state.mark_won()

        for peer_id in self._peers:
            self._send(
                peer_id,
                {
                    "type": "ELECTION_WIN",
                    "winner_id": self._node_id,
                    "zone": self._zone,
                    "term": term,
                    "timestamp": time.time(),
                },
            )

        self._on_win()

    def _timeout_handler(self, term: int) -> None:
        """Called when no ELECTION_OK arrived within the timeout window."""
        if self._state.in_election and not self._state.received_ok:
            log(
                "LeaderElection",
                f"election timeout -- no OK received; declaring victory (term={term})",
                channel="SYSTEM",
            )
            self._declare_victory(term)

    def _reset_timer(self, term: int) -> None:
        """Cancel any existing timer and start a new one for *term*."""
        self._cancel_timer()
        self._timer = threading.Timer(
            self._timeout, self._timeout_handler, args=(term,)
        )
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        """Cancel the current election timeout timer if alive."""
        if self._timer is not None and self._timer.is_alive():
            self._timer.cancel()
        self._timer = None

    def _send(self, target_node_id: str, payload: dict[str, Any]) -> None:
        """Publish *payload* to the target node's internal topic."""
        self._mqtt.publish(swarm_internal_topic(target_node_id), payload, qos=1)
