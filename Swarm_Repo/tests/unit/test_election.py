# TEST: U-ELEC-001 to U-ELEC-003



class TestElectionState:
    def test_accept_term_rejects_older_terms(self):
        from core.election.election_state import ElectionState
        state = ElectionState(initial_term=5)
        assert state.accept_term(3) == False
        assert state.term == 5
        assert state.in_election == False

    def test_accept_term_accepts_equal_term(self):
        from core.election.election_state import ElectionState
        state = ElectionState(initial_term=5)
        assert state.accept_term(5) == True
        assert state.term == 5
# State should NOT reset on equal term

    def test_accept_term_accepts_newer_term_and_resets(self):
        from core.election.election_state import ElectionState
        state = ElectionState(initial_term=5)
        state.mark_ok_received()
        assert state.received_ok == True
        assert state.accept_term(7) == True
        assert state.term == 7
# Should have reset election state
        assert state.in_election == False
        assert state.received_ok == False

    def test_start_election_bumps_term(self):
        from core.election.election_state import ElectionState
        state = ElectionState(initial_term=1)
        new_term = state.start_election()
        assert new_term == 2
        assert state.in_election == True
        assert state.term == 2

    def test_mark_won_clears_in_election(self):
        from core.election.election_state import ElectionState
        state = ElectionState(initial_term=1)
        state.start_election()
        state.mark_won()
        assert state.in_election == False
        assert state.won == True


class TestLeaderElection:
    def test_start_election_immediate_win_when_highest(self):
        from core.election.election_state import ElectionState
        from core.election.leader_election import LeaderElection
        from unittest.mock import MagicMock

        mqtt = MagicMock()
        on_win = MagicMock()
        on_lost = MagicMock()
        state = ElectionState(initial_term=1)

        election = LeaderElection(
            node_id="sl-Z", zone="zone_a",
            peer_ids=["sl-A", "sl-B"],
            state=state, mqtt=mqtt,
            on_win=on_win, on_lost=on_lost,
            timeout=5.0
        )
        election.start_election()
        # Should have won immediately - publishes ELECTION_WIN, not ELECTION_START
        start_calls = [c for c in mqtt.publish.call_args_list
                       if "ELECTION_START" in str(c.args)]
        assert len(start_calls) == 0
        on_win.assert_called_once()

    def test_handle_election_start_ignores_higher_peer(self):
        from core.election.election_state import ElectionState
        from core.election.leader_election import LeaderElection
        from unittest.mock import MagicMock

        mqtt = MagicMock()
        on_win = MagicMock()
        state = ElectionState(initial_term=1)

        election = LeaderElection(
            node_id="sl-B", zone="zone_a",
            peer_ids=["sl-A", "sl-C"],
            state=state, mqtt=mqtt,
            on_win=on_win, on_lost=MagicMock(),
            timeout=5.0
        )
# Simulate incoming ELECTION_START from a HIGHER node
        election._handle_election_start({"from_node_id": "sl-C", "term": 2})  # pyright: ignore[reportPrivateUsage]
# Should NOT reply with ELECTION_OK
        ok_calls = [c for c in mqtt.publish.call_args_list
                    if "ELECTION_OK" in str(c)]
        assert len(ok_calls) == 0
