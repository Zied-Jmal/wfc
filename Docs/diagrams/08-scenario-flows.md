# Test Scenario Coverage Map

Covers: how the 5 automated Docker Compose orchestrator scenarios exercise the system flows, what passes, and why.

## Coverage Matrix

| Scenario | Flow Diagrams Exercised | Stages | Status |
|---|---|---|---|
| 1. Fire Dispatch | [02-fire-dispatch](02-fire-dispatch.md), [01-node-lifecycle](01-node-lifecycle.md) | A–F (6) | PASSED |
| 2. Telemetry & Dashboard | [03-telemetry-pipeline](03-telemetry-pipeline.md), [07-dashboard-streaming](07-dashboard-streaming.md) | A–D (4) | PASSED |
| 3. Leader Election | [04-leader-election](04-leader-election.md), [01-node-lifecycle](01-node-lifecycle.md) | A–E (5) | PASSED |
| 4. Approval Gate | [05-approval-gate](05-approval-gate.md) | A–E (5) | PENDING FIX |
| 5. Node Lifecycle & LWT | [01-node-lifecycle](01-node-lifecycle.md) | A–E (5) | PASSED |

## Scenario 1: Fire Dispatch

```mermaid
sequenceDiagram
  participant Harness as Test Harness
  participant MQTT as Mosquitto
  participant App as WFC Application

  rect rgb(220, 240, 220)
    Note over Harness,App: Stage A: Trigger fire event
    Harness->>MQTT: publish wfc/events/fire
    MQTT-->>App: Commander dispatches RESPOND_TO_FIRE → sl-A-01
    Note right of App: Flow: 02-fire-dispatch (Stage A)
  end

  rect rgb(240, 240, 255)
    Note over Harness,App: Stage B: Leader ACK RECEIVED
    App->>MQTT: publish wfc/ack { status: "RECEIVED" }
  end

  rect rgb(255, 240, 220)
    Note over Harness,App: Stage C: Leader ACK EXECUTED + drone commands
    App->>App: FireTactics runs, _dispatch_assignments()
    App->>MQTT: UPDATE_TASK → sd-A-01, fd-A-01
    App->>MQTT: publish wfc/ack { status: "EXECUTED" }
  end

  rect rgb(220, 255, 220)
    Note over Harness,App: Stage D: Drone receives command (replay-matched)
    Note over Harness: Transcript replay catches UPDATE_TASK<br/>from Stage C's window
  end

  rect rgb(240, 240, 240)
    Note over Harness,App: Stage E+F: Drone ACKs + telemetry (replay-matched)
  end
```

**Key insight:** Stages D/E/F pass via MQTTBus transcript replay (the drone commands arrived during Stage B/C but Stage D's listener wasn't registered yet — the [engine fix](../orchestrator/mqtt_bus.py) replays recent transcript to new listeners).

## Scenario 2: Telemetry & Dashboard

```mermaid
sequenceDiagram
  participant Harness as Test Harness
  participant MQTT as Mosquitto
  participant App as WFC Application

  rect rgb(220, 240, 220)
    Note over Harness,App: Pre-stage: Publish NodeAnnouncement (ONLINE)
    Harness->>MQTT: publish (retained) wfc/registry/announce/test-scout-xxx
    Note right of Harness: REQUIRED — Dashboard rejects telemetry-only drones
  end

  rect rgb(240, 240, 255)
    Note over Harness,App: Stage A: Publish synthetic telemetry
    Harness->>MQTT: publish wfc/telemetry/test-scout-xxx
  end

  rect rgb(255, 240, 220)
    Note over Harness,App: Stage B: Leader status snapshot reflects drone
    App->>MQTT: wfc/swarm/status/sl-A-01 { active_drones >= 1 }
  end

  rect rgb(220, 255, 220)
    Note over Harness,App: Stage C: Dashboard REST API reflects drone
    Harness->>App: GET /api/nodes/test-scout-xxx (polling)
    App-->>Harness: 200 + node with battery_wh
    Note right of Harness: Requires NodeAnnouncement to have<br/>created NodeState first
  end

  rect rgb(240, 240, 240)
    Note over Harness,App: Stage D: Dashboard event log contains drone
    Harness->>App: GET /api/events?limit=50 (polling)
    App-->>Harness: events containing drone_id
  end
```

**Key fixes applied:**
1. Harness now publishes `NodeAnnouncement` (retained) before telemetry
2. Without this, `SwarmState.apply_telemetry()` silently drops unannounced drones

## Scenario 3: Leader Election

```mermaid
sequenceDiagram
  participant Harness as Test Harness
  participant Runner as Runner Script
  participant MQTT as Mosquitto
  participant App as WFC Application

  rect rgb(220, 240, 220)
    Note over Harness,App: Stage A: Spoof OFFLINE + Kill real leader
    Harness->>MQTT: publish retained OFFLINE for sl-A-01
    Runner->>App: docker stop sl-A-01 (from host)
    Note right of App: Real leader dies → heartbeats stop
  end

  rect rgb(255, 240, 220)
    Note over Harness,App: Stage B: Backup detects timeout (10s)
    App->>App: sl-A-02 _leader_monitor_loop<br/>elapsed > LEADER_HEARTBEAT_TIMEOUT
    App->>App: start_election() → immediate victory<br/>(sl-A-02 is highest-ID peer)
    App->>MQTT: publish wfc/swarm/internal/sl-A-01 { ELECTION_WIN }
  end

  rect rgb(240, 255, 240)
    Note over Harness,App: Stage C: Election won
    App->>MQTT: publish wfc/swarm/election/zone_alpha { new_leader_id }
  end

  rect rgb(240, 240, 255)
    Note over Harness,App: Stage D: New leader re-announces
    App->>MQTT: publish (retained) wfc/registry/announce/sl-A-02<br/>{ capabilities: ["SWARM_LEAD", ...] }
  end

  rect rgb(240, 240, 240)
    Note over Harness,App: Stage E: New leader publishes status snapshot
    App->>MQTT: publish wfc/swarm/status/sl-A-02
  end
```

**Key insight:** sl-A-02 is the highest-ID backup (`"sl-A-02" > "sl-A-01"`), so the bully protocol skips `ELECTION_START` and goes directly to `ELECTION_WIN`. Stage B was originally looking for `ELECTION_START` — fixed to match `ELECTION_WIN`.

## Scenario 4: Approval Gate

```mermaid
sequenceDiagram
  participant Harness as Test Harness
  participant MQTT as Mosquitto
  participant App as WFC Application

  rect rgb(255, 220, 220)
    Note over Harness,App: Stage A: Publish FireEvent (HIGH severity)
    Harness->>MQTT: publish wfc/events/fire<br/>{ severity: "HIGH" }
  end

  rect rgb(255, 240, 220)
    Note over Harness,App: Stage B: Commander creates pending approval
    App->>App: RuleEngine → ESCALATE_FIRE<br/>risk=IRREVERSIBLE → requires_approval
    App->>MQTT: publish wfc/approval/pending<br/>{ event: "COMMAND_PENDING" }
  end

  rect rgb(240, 255, 220)
    Note over Harness,App: Stage C: POST approval response
    Harness->>App: POST /api/approval/respond<br/>{ approved: true }
    App->>MQTT: publish wfc/approval/response
  end

  rect rgb(220, 255, 240)
    Note over Harness,App: Stage D: Command dispatched (ESCALATE_FIRE)
    App->>MQTT: publish wfc/command/{target}
  end

  rect rgb(240, 240, 255)
    Note over Harness,App: Stage E: Leader ACK
    App->>MQTT: publish wfc/ack
  end
```

**PENDING FIX:** The harness currently publishes `ABORT_MISSION` as a FireEvent, but `_handle_fire_event` in the commander doesn't route through the approval gate — it treats it as a fire dispatch. A HIGH-severity FireEvent should trigger `ESCALATE_FIRE` via the rule engine, which IS routed through the approval gate (risk=IRREVERSIBLE). The fix: use HIGH severity to trigger ESCALATE_FIRE instead of publishing ABORT_MISSION as a command.

## Scenario 5: Node Lifecycle & LWT

```mermaid
sequenceDiagram
  participant Harness as Test Harness
  participant MQTT as Mosquitto
  participant App as WFC Application

  rect rgb(220, 240, 220)
    Note over Harness,App: Stage A: Fake node connects (LWT set)
    Harness->>MQTT: connect with LWT { status: "OFFLINE" }
    Harness->>MQTT: publish retained ONLINE announcement
  end

  rect rgb(240, 240, 255)
    Note over Harness,App: Stage B: Dashboard shows ONLINE
    Harness->>App: GET /api/nodes/fake-node-xxx (polling)
    App-->>Harness: 200 + status="ONLINE"
  end

  rect rgb(255, 240, 220)
    Note over Harness,App: Stage C: Heartbeats + raw socket kill
    Harness->>MQTT: publish heartbeat (x2)
    Harness--xHarness: kill socket (no MQTT DISCONNECT)
  end

  rect rgb(255, 220, 220)
    Note over Harness,App: Stage D: LWT fires
    MQTT->>MQTT: detect ungraceful disconnect<br/>publish retained OFFLINE
  end

  rect rgb(240, 240, 240)
    Note over Harness,App: Stage E: Dashboard shows OFFLINE
    Harness->>App: GET /api/nodes/fake-node-xxx (polling)
    App-->>Harness: 200 + status="OFFLINE"
  end
```

## Flows NOT Covered by Orchestrator Scenarios

| Flow | Why Not Covered | Where Tested |
|---|---|---|
| Backup Commander failover | Requires stopping central-commander mid-run + waiting for backup promotion | Unit tests + SI/E2E subprocess tests |
| PriorityRule preemption | Requires concurrent fires + resource contention | Unit tests |
| Physical engine (GPS, wind, sensors, movement) | Requires drone simulator with physics | Not yet |
| Database persistence | No DB integration in orchestrated stack | Unit tests |
| Concurrent approval expiry vs operator decision | Requires timing-dependent race condition | Manual |
| Broker restart mid-flow | Requires broker lifecycle management | Not yet |

## See also

- [00-system-architecture.md](00-system-architecture.md) — System overview
- Flow diagrams 01–07 for detailed MQTT message sequences
