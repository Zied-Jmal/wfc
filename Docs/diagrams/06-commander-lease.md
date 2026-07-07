# Commander Lease & State Sync

Covers: Central Commander startup, lease acquisition, state snapshot broadcasting, backup promotion.

References: [01-node-lifecycle](01-node-lifecycle.md) for base node startup; [04-leader-election](04-leader-election.md) for contrast with swarm leader failover.

```mermaid
sequenceDiagram
  participant Central as Central Commander<br/>(central-commander)
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Backup as Backup Commander<br/>(backup-commander)
  participant HM as HeartbeatMonitor<br/>(Commander)
  participant Dash as Dashboard

  rect rgb(220, 240, 220)
    Note over Central,Dash: STARTUP (per 01-node-lifecycle)
    Central->>MQTT: set_will(OFFLINE)
    Central->>MQTT: connect + subscribe(wfc/#)
    Central->>MQTT: announce(ONLINE, retained)
    Central->>Central: CommanderCore.start(active=True)
    Central->>Central: _active = True
    Central->>Central: subscribe: APPROVAL_RESPONSE, SYSTEM_LEASE,<br/>  ACK, EVENTS_FIRE, STATE_SNAPSHOT,<br/>  SWARM_STATUS_SUB, SWARM_ELECTION_SUB,<br/>  FIRE_INTENSITY, FIRE_VERIFIED, FIRE_REKINDLED
  end

  rect rgb(255, 240, 220)
    Note over Central,Dash: LEASE CLAIM
    Central->>Central: sleep(1s) — wait for retained lease to arrive
    Central->>Central: _claim_or_reclaim_lease()
    Note over Central: no existing lease seen ⇒ term=1
    Central->>MQTT: publish (retained)<br/>topic: wfc/system/lease<br/>payload: { owner: "central-commander", term: 1,<br/>  since: now }
    MQTT-->>Backup: deliver lease
    Backup->>Backup: _lease_owner="central-commander", _lease_term=1
  end

  loop Every 10s
    rect rgb(240, 255, 240)
      Note over Central,Dash: STATE SNAPSHOT
      Central->>Central: _snapshot_loop()
      Central->>Central: assemble fires, missions, nodes, events_delta
      Central->>MQTT: publish<br/>topic: wfc/state/snapshot<br/>payload: { fires: [...], missions: [...],<br/>  nodes: {...}, events_delta: [...],<br/>  timestamp, source: "central-commander" }
      MQTT-->>Backup: deliver snapshot
      Backup->>Backup: _apply_snapshot()<br/>LWW merge by updated_at, INSERT OR IGNORE by event_id
      MQTT-->>Dash: deliver snapshot
      Dash->>Dash: apply_commander_snapshot()
    end
  end

  loop Every 5s
    rect rgb(220, 220, 255)
      Note over Central,Dash: LEASE RENEW
      Central->>MQTT: publish (retained)<br/>topic: wfc/system/lease<br/>payload: { owner: "central-commander",<br/>  term: 1, since: now }  (since stays same)
    end
  end
```

## Backup Promotion (Failover)

```mermaid
sequenceDiagram
  participant Central as Central Commander<br/>(central-commander)
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Backup as Backup Commander<br/>(backup-commander)
  participant HM as HeartbeatMonitor
  participant Dash as Dashboard

  rect rgb(255, 220, 220)
    Note over Central,Dash: CENTRAL CRASH
    Central--xCentral: crash / stop
    MQTT->>MQTT: LWT fires (after keepalive timeout)<br/>topic: wfc/registry/announce/central-commander<br/>payload: { node_id, status: "OFFLINE" }
  end

  rect rgb(255, 240, 220)
    Note over Central,Dash: HEARTBEAT TIMEOUT (Commander-level)
    HM-->>HM: update() not called for central-commander
    HM->>Backup: _on_node_failed("central-commander")
    Backup->>Backup: _primary_alive = False
    Backup->>Backup: _become_active()
  end

  rect rgb(240, 255, 240)
    Note over Central,Dash: LEASE STALENESS + PROMOTION
    loop Every 1s, up to 10s
      Backup->>Backup: _check_lease_and_maybe_promote()
      Note over Backup: elapsed = now - _last_lease_seen_at<br/>elapsed > LEASE_TTL (15s) ?
    end
    Backup->>Backup: lease stale → allowed to promote
    Backup->>Backup: CommanderCore.activate()
    Backup->>Backup: _active = True
    Backup->>Backup: _lease_term += 1 → term=2
    Backup->>MQTT: publish (retained)<br/>topic: wfc/system/lease<br/>payload: { owner: "backup-commander", term: 2, since: now }
    Backup->>MQTT: publish<br/>topic: wfc/system/failover<br/>payload: { new_primary: "backup-commander", timestamp }
    MQTT-->>Dash: deliver failover event
    Backup->>Backup: start _snapshot_loop
    Backup->>Backup: start _lease_renew_loop
  end
```

## Stand-down (Central Recovers)

```mermaid
sequenceDiagram
  participant Central as Central Commander
  participant MQTT as Mosquitto
  participant Backup as Backup Commander
  participant HM as HeartbeatMonitor

  Central->>MQTT: announce(ONLINE) again
  MQTT-->>Backup: deliver heartbeat
  HM->>Backup: _on_node_recovered("central-commander")
  Backup->>Backup: _stand_down()
  Backup->>Backup: core.deactivate()
  Backup->>Backup: _publish_snapshot_now() — one final snapshot
  Backup->>Backup: starts GRACE_PERIOD (30s) + stops _snapshot_loop
```

## Lease vs. Heartbeat Comparison

| Property | Commander Lease | Swarm Leader Heartbeat |
|---|---|---|
| **Mechanism** | Retained MQTT message + periodic renew | MQTT heartbeat event (every 5s) |
| **Timeout** | 15s (LEASE_TTL) | 10s (LEADER_HEARTBEAT_TIMEOUT) |
| **Promotion** | Increment term, publish new lease | Bully protocol election |
| **Detected by** | _check_lease_and_maybe_promote() | _leader_monitor_loop (1s poll) |
| **Scope** | System-wide (one active commander) | Per-zone (one active swarm leader) |

## Key File Paths

| File | Role |
|---|---|
| `Commander_Repo/command_nodes/core_commander/commander_core.py` | `_claim_or_reclaim_lease()`, `_snapshot_loop()`, `_lease_renew_loop()`, `activate()`, `deactivate()` |
| `Commander_Repo/command_nodes/central/services/node_runtime.py` | `CentralNode` — starts CommanderCore(active=True) |
| `Commander_Repo/command_nodes/backup/services/backup_commander.py` | `BackupCommander` — `_on_node_failed()`, `_become_active()`, `_stand_down()` |
| `Commander_Repo/core/node/base_node.py` | `HeartbeatMonitor` — node failure/recovery callbacks |

## See also

- [01-node-lifecycle.md](01-node-lifecycle.md) — Base node startup, heartbeat, LWT
- [04-leader-election.md](04-leader-election.md) — Swarm leader failover (different mechanism)
