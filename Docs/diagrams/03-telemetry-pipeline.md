# Telemetry Pipeline (G-04, G-06, G-09)

Covers: Drone telemetry publish → Leader aggregation → Dashboard ingestion + REST/SSE visibility.

References: [01-node-lifecycle](01-node-lifecycle.md) for node readiness; [02-fire-dispatch](02-fire-dispatch.md) for how drones become active.

```mermaid
sequenceDiagram
  participant Drone as Scout/Firefighting Drone
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Leader as SwarmLeaderNode<br/>(sl-A-01)
  participant TA as TelemetryAggregator
  participant Dash as Dashboard<br/>MQTTBridge + SwarmState
  participant Client as HTTP Client<br/>(UI / Test Harness)

  rect rgb(220, 240, 220)
    Note over Drone,Client: STAGE A: TELEMETRY PUBLISHED
    Drone->>MQTT: publish (every 2s)<br/>topic: wfc/telemetry/{drone_id}<br/>payload: DroneTelemetry V2(<br/>  drone_id, leader_id, timestamp,<br/>  position: [lat, lon],<br/>  altitude_m_amsl, battery_wh, battery_pct,<br/>  task, connectivity,<br/>  thermal_peak_temp_c, smoke_density_mg_m3,<br/>  flame_height_m, wind_speed_mps, ... )
    MQTT-->>Leader: deliver telemetry
    MQTT-->>Dash: deliver telemetry
  end

  rect rgb(240, 240, 255)
    Note over Drone,Client: STAGE B: LEADER INGESTS
    Leader->>Leader: _on_telemetry()<br/>filter: telem.leader_id == self.node_id
    Leader->>TA: ingest(telemetry)
    TA->>TA: update rolling stats<br/>(avg_battery, active_drones,<br/> intensity estimate, etc.)
    Leader->>Leader: _drone_reg.update_telemetry(drone_id, telemetry)
  end

  loop Every 10s (SWARM_STATUS_INTERVAL)
    rect rgb(255, 240, 220)
      Note over Drone,Client: STATUS SNAPSHOT
      Leader->>TA: snapshot()
      TA-->>Leader: SwarmStatusSnapshot(<br/>  active_drones, avg_battery_pct,<br/>  fire_intensity, swarm_status, ... )
      Leader->>MQTT: publish<br/>topic: wfc/swarm/status/{leader_id}<br/>payload: SwarmStatusSnapshot( leader_id, active_drones,<br/>  intensity, fire_id, ... )
      MQTT-->>Dash: deliver snapshot
    end
  end

  rect rgb(220, 255, 220)
    Note over Drone,Client: STAGE C: DASHBOARD INGESTS
    Dash->>Dash: _on_message() → topic matches wfc/telemetry/{drone_id}
    Dash->>Dash: DroneTelemetry(**raw)
    Dash->>Dash: swarm_state.apply_telemetry(telemetry)
    Dash->>Dash: if node exists in _nodes:<br/>  update location, battery, task, connectivity, etc.
    Note over Dash: NOTE: node must exist via<br/>prior apply_announcement()<br/>Telemetry-only drones are rejected

    Dash->>Dash: _on_message() → topic matches wfc/swarm/status/{leader_id}
    Dash->>Dash: SwarmStatusSnapshot(**raw)
    Dash->>Dash: swarm_state.apply_swarm_status(leader_id, snapshot)
    Dash->>Dash: upsert_fire_from_snapshot(fire_id, leader_id, intensity)
  end

  rect rgb(240, 240, 240)
    Note over Drone,Client: STAGE D: REST / SSE VISIBILITY
    Client->>Dash: GET /api/nodes/{drone_id}
    Dash-->>Client: 200 OK + NodeState.as_dict()<br/>{ node_id, node_type, status, location,<br/>  battery_wh, task, connectivity, ... }

    Client->>Dash: GET /api/fires
    Dash-->>Client: [ FireRecord.as_dict(), ... ]

    Client->>Dash: GET /api/events?limit=50
    Dash-->>Client: [ {type, node_id, fire_id, ts}, ... ]

    Client->>Dash: GET /api/stream (SSE)
    Dash-->>Client: data: { nodes: [...], fires: [...],<br/>  new_events: [...], ts: ... }\n\n
    Note over Client: SSE yields full snapshot every 1s
  end
```

## SwarmState Ingestion Map

| Incoming MQTT Topic | SwarmState Method | Creates Node? |
|---|---|---|
| `wfc/registry/announce/{id}` | `apply_announcement()` | Yes — creates NodeState |
| `wfc/telemetry/{id}` | `apply_telemetry()` | No — silently dropped if node doesn't exist |
| `wfc/swarm/status/{id}` | `apply_swarm_status()` | No — silently dropped if node doesn't exist |
| `wfc/events/fire` | `apply_fire_event()` | Yes — creates FireRecord |
| `wfc/state/snapshot` | `apply_commander_snapshot()` | Yes — creates NodeState for commander |
| `wfc/approval/pending` | `add_pending_approval()` | No |

## Key Design Constraint

**Telemetry-only nodes are invisible to the Dashboard.** A `NodeState` entry is created only by `NodeAnnouncement` (topic `wfc/registry/announce/{id}`). Drone telemetry on `wfc/telemetry/{id}` without a prior announcement is silently dropped. This means test harnesses must publish a retained `NodeAnnouncement` BEFORE publishing synthetic telemetry.

## SwarmStatusSnapshot Payload

```json
{
  "leader_id": "sl-A-01",
  "active_drones": 3,
  "lost_drones": 0,
  "avg_battery_pct": 0.82,
  "min_battery_wh": 420.0,
  "avg_payload_litres": 5.0,
  "total_litres_delivered": 120.0,
  "fire_id": "abc123",
  "fire_intensity": "HIGH",
  "swarm_status": "ACTIVE",
  "suppression_pct": 35.0,
  "spread_rate": "MODERATE",
  "perimeter_estimate": 250.0,
  "wind_speed_mps_snap": 4.0,
  "wind_direction_deg_snap": 200.0
}
```

## Key File Paths

| File | Role |
|---|---|
| `Swarm_Repo/core/node/swarm_leader_node.py:378` | `_on_telemetry()` — leader ingests drone telemetry |
| `Swarm_Repo/core/aggregator/telemetry_aggregator.py` | `ingest()`, `snapshot()`, rolling stats |
| `Dashboard_Repo/dashboard/mqtt_bridge.py` | MQTT subscriptions, message routing to SwarmState |
| `Dashboard_Repo/dashboard/state.py:247` | `apply_announcement()` — creates NodeState |
| `Dashboard_Repo/dashboard/state.py:255` | `apply_telemetry()` — rejects unannounced drones |
| `Dashboard_Repo/dashboard/state.py:262` | `apply_swarm_status()` — stores leader snapshot |
| `Dashboard_Repo/dashboard/server.py` | REST endpoints + SSE stream |

## See also

- [01-node-lifecycle.md](01-node-lifecycle.md) — Nodes must be announced before telemetry is accepted
- [07-dashboard-streaming.md](07-dashboard-streaming.md) — SSE stream details
- [02-fire-dispatch.md](02-fire-dispatch.md) — How drones become active and start telemetry
