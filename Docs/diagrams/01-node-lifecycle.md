# Node Lifecycle

Covers: startup, announce, heartbeat, graceful stop, LWT crash detection.

Referenced by: [02-fire-dispatch](02-fire-dispatch.md), [03-telemetry-pipeline](03-telemetry-pipeline.md), [04-leader-election](04-leader-election.md)

```mermaid
sequenceDiagram
  participant Node as Any Node<br/>(Drone/Leader/Commander)
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Registry as NodeRegistry<br/>(Commander)
  participant Dash as Dashboard<br/>SwarmState

  rect rgb(220, 240, 220)
    Note over Node,Dash: STARTUP
    Node->>MQTT: set_will( topic=wfc/registry/announce/{node_id},<br/>payload={status: "OFFLINE"}, retain=true )
    Node->>MQTT: connect()
    Node->>MQTT: subscribe( wfc/registry/announce/# )
    Node->>MQTT: publish (retained)<br/>topic: wfc/registry/announce/{node_id}<br/>payload: NodeAnnouncement( status="ONLINE",<br/>  capabilities=[...], zone=..., location=... )
    MQTT-->>Registry: notify announce
    MQTT-->>Dash: notify announce
    Registry->>Registry: register(node_id, status="REGISTERED")
  end

  loop Every 5s
    rect rgb(240, 240, 255)
      Note over Node,Dash: HEARTBEAT
      Node->>MQTT: publish<br/>topic: wfc/nodes/{node_id}/heartbeat<br/>payload: { node_id, type, timestamp, status: "alive" }
      MQTT-->>Registry: notify heartbeat
      Registry->>Registry: heartbeat(node_id) → status="ACTIVE"
      MQTT-->>Dash: update_heartbeat(node_id)
    end
  end

  rect rgb(255, 240, 240)
    Note over Node,Dash: GRACEFUL STOP
    Node->>MQTT: publish (retained)<br/>topic: wfc/registry/announce/{node_id}<br/>payload: NodeAnnouncement( status="OFFLINE" )
    MQTT-->>Registry: notify announce
    MQTT-->>Dash: notify announce
    Registry->>Registry: mark_offline(node_id)
    Dash->>Dash: mark_offline(node_id)
  end

  rect rgb(255, 220, 220)
    Note over Node,Dash: CRASH / LWT
    Node--xNode: ungraceful disconnect
    MQTT->>MQTT: LWT fires
    MQTT->>MQTT: publish (retained)<br/>topic: wfc/registry/announce/{node_id}<br/>payload: { status: "OFFLINE" }
    MQTT-->>Registry: notify announce
    MQTT-->>Dash: notify announce
    Registry->>Registry: mark_offline(node_id)
    Dash->>Dash: mark_offline(node_id)
  end
```

## Timeline

| Phase | What happens | Key MQTT Topic | Key Message |
|---|---|---|---|
| Startup | Set LWT, connect, announce ONLINE | `wfc/registry/announce/{id}` | `{status: "ONLINE", capabilities: [...], zone: ..., location: ...}` |
| Heartbeat | Periodic keepalive (5s) | `wfc/nodes/{id}/heartbeat` | `{status: "alive", timestamp: ...}` |
| ACTIVE promotion | After first heartbeat | (internal NodeRegistry) | `register()` → `REGISTERED`, `heartbeat()` → `ACTIVE` |
| Graceful stop | Manual shutdown | `wfc/registry/announce/{id}` | `{status: "OFFLINE"}` (retained) |
| LWT | Broker publishes on ungraceful disconnect | `wfc/registry/announce/{id}` | `{status: "OFFLINE"}` (retained, set at connect) |

## Related File Paths

| File | Role |
|---|---|
| `Swarm_Repo/core/node/field_node.py` | Base node class: `start()`, `_announce()`, `_send_heartbeat()`, `stop()` |
| `Swarm_Repo/core/node/base_node.py` | `BaseNode` with MQTT lifecycle, heartbeat, LWT setup |
| `Swarm_Repo/core/node/heartbeat.py` | `Heartbeat` timer (interval=5s) |
| `Commander_Repo/core/node_registry/registry.py` | `register()` → REGISTERED, `heartbeat()` → ACTIVE |
| `Dashboard_Repo/dashboard/state.py` | `apply_announcement()`, `mark_offline()` |

## See also

- [00-system-architecture.md](00-system-architecture.md) — Component reference
- [02-fire-dispatch.md](02-fire-dispatch.md) — Uses node lifecycle for drone readiness
- [04-leader-election.md](04-leader-election.md) — Depends on heartbeat timeout detection
