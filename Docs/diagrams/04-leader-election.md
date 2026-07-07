# Leader Election (G-07, G-08) — Bully Protocol Failover

Covers: active leader failure → backup heartbeat timeout → bully election → new leader promoted.

References: [01-node-lifecycle](01-node-lifecycle.md) for heartbeat/LWT; [06-commander-lease](06-commander-lease.md) for comparison with commander-level failover.

```mermaid
sequenceDiagram
  participant Active as Active Leader<br/>(sl-A-01)
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Backup as Backup Leader<br/>(sl-A-02)
  participant Monitor as Backup Monitor<br/>_leader_monitor_loop
  participant LE as LeaderElection
  participant Peer as Dead Peer<br/>(sl-A-01 internal topic)
  participant Dash as Dashboard

  rect rgb(220, 240, 220)
    Note over Active,Dash: NORMAL OPERATION
    loop Every 5s
      Active->>MQTT: publish<br/>topic: wfc/nodes/sl-A-01/heartbeat<br/>payload: { node_id: "sl-A-01", type: "SWARM_LEADER", timestamp }
      MQTT-->>Backup: deliver heartbeat
      Backup->>Backup: _leader_last_seen = time.time()
    end
  end

  rect rgb(255, 220, 220)
    Note over Active,Dash: STAGE A: LEADER DIES
    Active--xActive: container stopped / crash
    MQTT->>MQTT: LWT fires (if ungraceful)<br/>topic: wfc/registry/announce/sl-A-01<br/>payload: { status: "OFFLINE" }
  end

  rect rgb(255, 240, 220)
    Note over Active,Dash: STAGE B: HEARTBEAT TIMEOUT
    loop Every 1s
      Monitor->>Monitor: elapsed = now - _leader_last_seen
      Note right of Monitor: elapsed > LEADER_HEARTBEAT_TIMEOUT (10s)?
    end
    Monitor->>Monitor: elapsed=10.5s > 10s → TIMEOUT
    Monitor->>Monitor: _current_leader_id = None
    Monitor->>LE: start_election()
  end

  rect rgb(240, 255, 240)
    Note over Active,Dash: STAGE C: ELECTION (HIGHEST-ID WINS IMMEDIATELY)
    LE->>LE: higher_peers = peers > self.node_id<br/>sl-A-01 < sl-A-02 → no higher peers
    LE->>LE: no higher peers → _declare_victory()
    LE->>LE: _state.mark_won()
    LE->>MQTT: publish ELECTION_WIN<br/>topic: wfc/swarm/internal/sl-A-01<br/>payload: { type: "ELECTION_WIN", winner_id: "sl-A-02",<br/>  zone: "zone_alpha", term: 2 }
    Note over Peer: sent to dead leader's topic<br/>(logged in transcript but no consumer)
  end

  rect rgb(220, 220, 255)
    Note over Active,Dash: STAGE D: WINNER RE-ANNOUNCES
    LE->>LE: _on_win() callback
    Backup->>Backup: _on_election_win()
    Backup->>Backup: capabilities: remove LEADER_BACKUP, add SWARM_LEAD
    Backup->>Backup: _is_backup = False
    Backup->>MQTT: publish (retained)<br/>topic: wfc/registry/announce/sl-A-02<br/>payload: NodeAnnouncement( status: "ONLINE",<br/>  capabilities: ["SWARM_LEAD", ...],<br/>  election: { type: "BULLY", term: 2, previous_leader: "sl-A-01" } )
    MQTT-->>Dash: notify announce

    Backup->>MQTT: publish<br/>topic: wfc/swarm/election/zone_alpha<br/>payload: { new_leader_id: "sl-A-02",<br/>  old_leader_id: "sl-A-01",<br/>  election_type: "BULLY", term: 2, fire_id, swarm_size }
    MQTT-->>Dash: notify election event
  end

  rect rgb(240, 240, 240)
    Note over Active,Dash: STAGE E: NEW LEADER RESUMES OPERATIONS
    Note over Backup: _status_publish_loop starts<br/>(was already running, now publishes with SWARM_LEAD)
    Backup->>MQTT: publish<br/>topic: wfc/swarm/status/sl-A-02<br/>payload: SwarmStatusSnapshot( leader_id: "sl-A-02",<br/>  active_drones, fire_id, ... )
    MQTT-->>Dash: deliver snapshot
    Dash->>Dash: apply_swarm_status("sl-A-02", snapshot)
  end
```

## Bully Protocol Variant: sl-A-01 (lower ID) detects leader death

If the lower-ID node (sl-A-01) were the backup and sl-A-02 the active leader died:

```
sl-A-01._leader_monitor_loop detects timeout
  → LE.start_election()
  → higher_peers = [sl-A-02] (higher ID ❯ "sl-A-01" < "sl-A-02")
  → Send ELECTION_START to wfc/swarm/internal/sl-A-02
  → Wait ELECTION_TIMEOUT (5s)
  → If no ELECTION_OK received → _declare_victory()
```

In our current setup, sl-A-02 has the higher ID, so it skips directly to victory (no ELECTION_START is ever published).

## Configuration

| Env Var | Default | Purpose |
|---|---|---|
| `LEADER_HEARTBEAT_TIMEOUT` | `10` | Seconds without a leader heartbeat before triggering election |
| `ELECTION_TIMEOUT` | `5` | Seconds to wait for ELECTION_OK from higher peers |
| `SWARM_STATUS_INTERVAL` | `10` | Seconds between SwarmStatusSnapshot publishes |
| `IS_BACKUP` | `false` | If `"true"`, node starts in backup mode (no SWARM_LEAD cap) |
| `BACKUP_PEERS` | `""` | Comma-separated peer IDs (e.g. `"sl-A-01"`) |

## Key File Paths

| File | Role |
|---|---|
| `Swarm_Repo/core/node/swarm_leader_node.py:484` | `_leader_monitor_loop()` — 1s check for heartbeat timeout |
| `Swarm_Repo/core/election/leader_election.py:62` | `start_election()` — bully protocol entry |
| `Swarm_Repo/core/election/leader_election.py:168` | `_declare_victory()` — publishes ELECTION_WIN + triggers _on_win |
| `Swarm_Repo/core/node/swarm_leader_node.py:506` | `_on_election_win()` — re-announces with SWARM_LEAD |
| `Swarm_Repo/core/election/leader_election.py:204` | `_send()` — publishes to `wfc/swarm/internal/{target}` |

## See also

- [01-node-lifecycle.md](01-node-lifecycle.md) — Heartbeat mechanism, LWT, OFFLINE detection
- [06-commander-lease.md](06-commander-lease.md) — Commander-level failover (different mechanism using lease)
