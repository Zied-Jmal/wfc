# Dashboard SSE & REST API

Covers: how the dashboard serves data via REST endpoints and Server-Sent Events streaming.

References: [03-telemetry-pipeline](03-telemetry-pipeline.md) for how SwarmState gets populated.

```mermaid
sequenceDiagram
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant MB as MQTTBridge
  participant SS as SwarmState<br/>(in-memory)
  participant API as REST API<br/>(server.py)
  participant SSE as SSE Stream<br/>(/api/stream)
  participant Client as HTTP Client<br/>(UI / Test Harness)

  rect rgb(240, 240, 240)
    Note over MQTT,Client: MQTT INGESTION (background, continuous)
    MQTT->>MB: deliver any wfc/# message
    MB->>MB: _on_message() — parse JSON
    MB->>MB: route by topic prefix:
    MB->>SS: apply_announcement() — wfc/registry/announce/+
    MB->>SS: apply_telemetry() — wfc/telemetry/+
    MB->>SS: apply_swarm_status() — wfc/swarm/status/+
    MB->>SS: apply_fire_event() — wfc/events/fire
    MB->>SS: add_pending_approval() — wfc/approval/pending
    MB->>SS: add_election_event() — wfc/swarm/election/+
    MB->>SS: add_event() — wfc/ack, wfc/system/failover, etc.
    MB->>SS: apply_commander_snapshot() — wfc/state/snapshot
  end

  rect rgb(220, 240, 220)
    Note over MQTT,Client: REST ENDPOINTS
    Client->>API: GET /api/nodes
    API->>SS: get_all_nodes()
    SS-->>API: [NodeState.as_dict(), ...]
    API-->>Client: 200 OK + node list

    Client->>API: GET /api/nodes/{node_id}
    API->>SS: get_node(node_id)
    SS-->>API: NodeState.as_dict() or None
    API-->>Client: 200 OK + node data | 404 Not Found

    Client->>API: GET /api/fires
    API->>SS: get_all_fires()
    SS-->>API: [FireRecord.as_dict(), ...]
    API-->>Client: 200 OK + fire list

    Client->>API: GET /api/events?limit=N
    API->>SS: get_events(N)
    SS-->>API: [event_dict, ...] (newest first)
    API-->>Client: 200 OK + event list

    Client->>API: GET /api/approvals
    API->>SS: get_pending_approvals()
    SS-->>API: [pending_approval_dict, ...]
    API-->>Client: 200 OK + pending list

    Client->>API: POST /api/approval/respond<br/>{ pending_id, approved, reason }
    API->>API: parse ApprovalResp
    API->>MQTT: publish to wfc/approval/response<br/>(see 05-approval-gate.md)
    API-->>Client: 200 OK + { status: "submitted" }

    Client->>API: GET /api/elections
    API->>SS: get_election_events()
    SS-->>API: [election_event, ...]
    API-->>Client: 200 OK
  end

  rect rgb(255, 240, 220)
    Note over MQTT,Client: SSE STREAM (/api/stream)
    Client->>SSE: GET /api/stream<br/>Accept: text/event-stream
    SSE-->>Client: HTTP 200<br/>Content-Type: text/event-stream<br/>Cache-Control: no-cache<br/>X-Accel-Buffering: no

    loop Every 1 second
      SSE->>SS: get_all_nodes()
      SSE->>SS: get_all_fires()
      SSE->>SS: get_events(50)
      SSE->>SSE: compute new_events = events[last_count:]
      SSE->>SSE: last_count = len(events)
      SSE-->>Client: data: {<br/>  "nodes": [...],          # full node snapshot<br/>  "fires": [...],          # full fire snapshot<br/>  "new_events": [...],     # delta since last poll<br/>  "ts": 1234567890.0       # server timestamp<br/>}\n\n
    end

    Note over SSE,Client: Client receives push every 1s<br/>No polling needed for live view
  end
```

## REST API Endpoint Reference

| Method | Endpoint | Handler | Returns |
|---|---|---|---|
| GET | `/api/nodes` | `get_nodes()` | `list[NodeState]` |
| GET | `/api/nodes/{node_id}` | `get_node()` | `NodeState` or 404 |
| GET | `/api/fires` | `get_fires()` | `list[FireRecord]` |
| GET | `/api/events?limit=N` | `get_events()` | `list[event_dict]` |
| GET | `/api/approvals` | `get_approvals()` | `list[pending_approval]` |
| POST | `/api/approval/respond` | `approval_respond()` | `{status: "submitted"}` |
| GET | `/api/elections` | `get_elections()` | `list[election_event]` |
| GET | `/api/stream` | `sse_stream()` | SSE `text/event-stream` |

## NodeState Fields (as returned by `as_dict()`)

```json
{
  "node_id": "sl-A-01",
  "node_type": "SWARM_LEADER",
  "capabilities": ["RECEIVE_COMMANDS", "HEARTBEAT", "TELEMETRY", "SWARM_LEAD"],
  "status": "ONLINE",
  "zone": "zone_alpha",
  "location": [36.8065, 10.1815],
  "announced_at": 1234567890.0,
  "last_seen": 1234567895.0,
  "task": null,
  "connectivity": null,
  "battery_wh": null,
  "altitude_m_amsl": null,
  "battery_pct": null,
  // ... telemetry fields (set after apply_telemetry)
}
```

## SSE Stream Format

```
data: {"nodes":[...],"fires":[...],"new_events":[...],"ts":1234567890.0}\n\n
```

- Delivered every 1 second
- `nodes` is a FULL snapshot (not delta) — client replaces its local list
- `fires` is also a FULL snapshot
- `new_events` is a DELTA (events since the client's last read, capped at 50)
- Client should handle reconnection with `Last-Event-ID` support (optional)

## Key File Paths

| File | Role |
|---|---|
| `Dashboard_Repo/dashboard/server.py` | All REST endpoints + SSE stream generator |
| `Dashboard_Repo/dashboard/mqtt_bridge.py` | `_on_message()` — routes MQTT to SwarmState |
| `Dashboard_Repo/dashboard/state.py` | `SwarmState`, `NodeState`, `FireRecord`, `as_dict()` |

## See also

- [03-telemetry-pipeline.md](03-telemetry-pipeline.md) — How SwarmState gets populated from MQTT
- [05-approval-gate.md](05-approval-gate.md) — Approval REST endpoints in context
