"""
SCENARIO 3 \u2014 Leader Election (Bully Protocol) Failover
=========================================================
Validates G-07, G-08. Simulates an active leader dying and confirms a
backup correctly wins the election and announces itself.
This scenario does NOT kill a real container (that's destructive and hard
to make idempotent across re-runs). Instead it simulates death by
withholding the heartbeat: it spoofs an OFFLINE announcement for the
target leader, which is the same signal real backups react to.

  Stage A   Harness publishes a synthetic OFFLINE announcement + stops
            "heartbeating" on behalf of the (real) active leader being
            tested \u2014 backups should notice via their own timeout logic
            using the REAL leader's last real heartbeat, so this stage
            just confirms the OFFLINE announce was delivered.
  Stage B   A backup leader starts an election \u2014 observed via a message
            on wfc/swarm/internal/{some_node} with type=ELECTION_START.
  Stage C   A backup leader declares victory \u2014 observed via
            wfc/swarm/election/{zone} with a new_leader_id field.
  Stage D   The winning leader re-announces itself with SWARM_LEAD in
            its capabilities (retained registry slot).
  Stage E   The new leader resumes operations \u2014 confirmed by it
            publishing its first SwarmStatusSnapshot post-election.

NOTE: this scenario assumes you've deployed at least 2 swarm leaders for
the same zone where one is_backup=True (see WFC_INTEGRATION_GUIDE for the
backup_peers / is_backup env vars). If you only run a single leader,
stages B-E will correctly time out, telling you immediately that no
backup is configured \u2014 not that election logic is broken.

"""

from __future__ import annotations

import time
from typing import Any

from orchestrator.engine import Scenario, Stage


def build(zone: str = "zone_alpha", target_leader_id: str = "sl-A-01") -> Scenario:

    def spoof_leader_offline(ctx: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_id": target_leader_id,
            "node_type": "SWARM_LEADER",
            "capabilities": [],
            "status": "OFFLINE",
            "announced_at": time.time(),
            "zone": zone,
        }
        ctx["_publish_now"] = (
            f"wfc/registry/announce/{target_leader_id}",
            payload,
            1,
            True,
        )
        return {"offline_spoofed_at": time.time()}

    stage_a = Stage(
        stage_id="A_offline_announced",
        name=f"Synthetic OFFLINE announced for {target_leader_id}",
        component="Test Harness (failure injector)",
        expect_desc=f"Harness publishes retained OFFLINE announcement for {target_leader_id}",
        subscribe_topics=[f"wfc/registry/announce/{target_leader_id}"],
        timeout_s=5.0,
        on_enter=spoof_leader_offline,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == target_leader_id
            and payload.get("status") == "OFFLINE"
        ),
    )

    stage_b = Stage(
        stage_id="B_election_won",
        name="Backup leader declares victory (highest-ID peer skips ELECTION_START)",
        component="LeaderElection._declare_victory (Swarm repo)",
        expect_desc="Highest-ID backup (sl-A-02) broadcasts ELECTION_WIN on wfc/swarm/internal/{peer_id}",
        subscribe_topics=["wfc/swarm/internal/+"],
        timeout_s=25.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict) and payload.get("type") == "ELECTION_WIN"  # pyright: ignore[reportUnnecessaryIsInstance]
        ),
        extract_ctx=lambda topic, payload, ctx: {
            "election_initiator": payload.get("winner_id"),
            "election_term": payload.get("term"),
            "new_leader_id": payload.get("winner_id"),
        },
        on_skip=lambda ctx: None,
    )

    stage_c = Stage(
        stage_id="C_election_won",
        name="A backup leader declares victory",
        component="LeaderElection._declare_victory",
        expect_desc=f"A message on wfc/swarm/election/{zone} carries new_leader_id",
        subscribe_topics=[f"wfc/swarm/election/{zone}"],
        timeout_s=10.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict) and bool(payload.get("new_leader_id"))  # pyright: ignore[reportUnnecessaryIsInstance]
        ),
        extract_ctx=lambda topic, payload, ctx: {
            "new_leader_id": payload.get("new_leader_id"),
        },
    )

    stage_d = Stage(
        stage_id="D_new_leader_announced",
        name="Winning leader re-announces with SWARM_LEAD",
        component="SwarmLeaderNode._on_election_win",
        expect_desc="Retained registry announce for the new leader includes SWARM_LEAD capability",
        subscribe_topics=["wfc/registry/announce/+"],
        timeout_s=8.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("node_id") == ctx.get("new_leader_id")
            and "SWARM_LEAD" in (payload.get("capabilities") or [])
        ),
    )

    stage_e = Stage(
        stage_id="E_new_leader_resumes_ops",
        name="New leader publishes its first post-election SwarmStatusSnapshot",
        component="SwarmLeaderNode._status_publish_loop",
        expect_desc="The newly elected leader publishes a SwarmStatusSnapshot, proving it resumed normal operation",
        subscribe_topics=["wfc/swarm/status/+"],
        timeout_s=15.0,
        match_fn=lambda topic, payload, ctx: (
            isinstance(payload, dict)  # pyright: ignore[reportUnnecessaryIsInstance]
            and payload.get("leader_id") == ctx.get("new_leader_id")
        ),
    )

    return Scenario(
        scenario_id="leader_election",
        title="Leader Election Failover",
        description=(
            "Simulates the active leader going OFFLINE and verifies a backup "
            "correctly runs the bully election protocol, wins, announces "
            "itself, and resumes publishing status snapshots."
        ),
        stages=[stage_a, stage_b, stage_c, stage_d, stage_e],
    )
