# Approval Gate (G-10)

Covers: High-risk command detection → Pending approval published → Operator response → Dispatch or reject.

References: [02-fire-dispatch](02-fire-dispatch.md) for how SAFE commands bypass this flow.

```mermaid
sequenceDiagram
  participant Commander as CommanderCore
  participant RE as RuleEngine
  participant Gate as ApprovalGate
  participant Store as PendingCommandStore
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Dash as Dashboard<br/>SwarmState
  participant Operator as Human Operator<br/>(Dashboard UI)

  rect rgb(220, 240, 220)
    Note over Commander,Operator: HIGH-RISK COMMAND DETECTED
    RE->>RE: fire 'high_severity' rule fires<br/>command_type = ESCALATE_FIRE<br/>risk = IRREVERSIBLE
    RE->>Gate: submit(command, requires_approval=True)
    Gate->>Gate: create PendingCommand(pending_id, command, status=PENDING)
    Gate->>Store: add(pending)
  end

  rect rgb(255, 240, 220)
    Note over Commander,Operator: COMMAND_PENDING PUBLISHED
    Store->>MQTT: publish<br/>topic: wfc/approval/pending<br/>payload: { event: "COMMAND_PENDING",<br/>  pending_id, command_type: "ESCALATE_FIRE",<br/>  target_node, payload, created_at,<br/>  expires_at: created_at + 30s,<br/>  source: "central-commander" }
    MQTT-->>Dash: deliver pending
    Dash->>Dash: swarm_state.add_pending_approval(raw)
  end

  rect rgb(240, 255, 220)
    Note over Commander,Operator: OPERATOR SEES + DECIDES

    Operator->>Dash: GET /api/approvals
    Dash-->>Operator: [ { pending_id, command_type,<br/>  target_node, created_at, expires_at }, ... ]

    Operator->>Dash: POST /api/approval/respond<br/>payload: { pending_id, approved: true,<br/>  reason: "Escalation justified" }

    Dash->>Dash: parse ApprovalResp
    Dash->>MQTT: publish<br/>topic: wfc/approval/response<br/>payload: { pending_id, decision: "APPROVED",<br/>  operator_id: "ctrl-dashboard", timestamp }
  end

  rect rgb(220, 220, 255)
    Note over Commander,Operator: APPROVAL HANDLED
    MQTT-->>Commander: deliver approval response
    Commander->>Commander: route to ApprovalHandler.handle()
    Handler->>Handler: payload.decision == "APPROVED"
    Handler->>Store: approve(pending_id)
    Store->>Store: retrieve stored command
    Store->>MQTT: publish<br/>topic: wfc/approval/pending<br/>payload: { event: "COMMAND_APPROVED",<br/>  pending_id, trace_id, operator_id, decided_at }
    Store->>Commander: dispatcher.send(command)
    Commander->>MQTT: publish<br/>topic: wfc/command/{target_node}<br/>payload: { command_type, trace_id, target_node, ... }
    Note over Commander: command now dispatched to target node<br/>(continues per 02-fire-dispatch ACK protocol)
    MQTT-->>Dash: deliver COMMAND_APPROVED
    Dash->>Dash: update pending approval status
  end
```

## Rejected / Expired Path

```mermaid
sequenceDiagram
  participant Operator as Human Operator
  participant Dash as Dashboard
  participant MQTT as Mosquitto
  participant Commander as CommanderCore
  participant Store as PendingCommandStore

  Operator->>Dash: POST /api/approval/respond<br/>{ pending_id, approved: false, reason: "Unsafe" }
  Dash->>MQTT: publish<br/>topic: wfc/approval/response<br/>payload: { decision: "REJECTED", reason: "Unsafe", ... }
  MQTT-->>Commander: deliver
  Commander->>Store: reject(pending_id)
  Store->>MQTT: publish<br/>topic: wfc/approval/pending<br/>payload: { event: "COMMAND_REJECTED",<br/>  pending_id, reason: "operator_rejected", ... }
  Note over Commander,Store: Command is NOT dispatched<br/>Dropped silently

  loop Every 5s (_expire_loop)
    Store->>Store: expire_stale()
    Store->>MQTT: publish<br/>topic: wfc/approval/pending<br/>payload: { event: "COMMAND_EXPIRED",<br/>  pending_id, reason: "ttl_expired", ... }
    Note over Store: COMMAND_EXPIRED for any pending<br/>older than TTL (30s)
  end
```

## Risk Levels & Command Routing

```mermaid
flowchart LR
  subgraph RuleEngine
    A[Rule triggers]
    B{risk?}
  end
  A --> B
  B -->|SAFE| C[Auto-dispatch<br/>via dispatcher.send()]
  B -->|DISRUPTIVE| D[ApprovalGate<br/>requires approval]
  B -->|IRREVERSIBLE| D
  D --> E{PendingStore}
  E -->|approved| C
  E -->|rejected| F[Write event log<br/>No dispatch]
  E -->|expired| F
```

## Key Payloads

| Event | Topic | Key Fields |
|---|---|---|
| `COMMAND_PENDING` | `wfc/approval/pending` | `pending_id`, `command_type`, `target_node`, `created_at`, `expires_at` |
| `COMMAND_APPROVED` | `wfc/approval/pending` | `pending_id`, `trace_id`, `operator_id`, `decided_at` |
| `COMMAND_REJECTED` | `wfc/approval/pending` | `pending_id`, `reason`, `operator_id`, `decided_at` |
| `COMMAND_EXPIRED` | `wfc/approval/pending` | `pending_id`, `reason: "ttl_expired"`, `decided_at` |
| Operator decision | `wfc/approval/response` | `pending_id`, `decision`: `"APPROVED"` / `"REJECTED"`, `operator_id`, `reason` |

## Key File Paths

| File | Role |
|---|---|
| `Commander_Repo/core/approval/approval_gate.py` | `submit()` — routes to dispatcher or PendingStore |
| `Commander_Repo/core/approval/pending_store.py` | `add()`, `approve()`, `reject()`, `expire_stale()` |
| `Commander_Repo/core/approval/approval_handler.py` | `handle()` — processes APPROVAL_RESPONSE |
| `Commander_Repo/core/rule_engine/engine.py` | `evaluate()` — sets `requires_approval` |
| `Commander_Repo/command_nodes/core_commander/commander_core.py:835` | `_handle_command()` — approval gate routing |
| `wfc_shared/wfc_shared/enums/command_risk.py` | `COMMAND_RISK` dict |
| `Dashboard_Repo/dashboard/server.py` | `GET /api/approvals`, `POST /api/approval/respond` |

## See also

- [02-fire-dispatch.md](02-fire-dispatch.md) — How SAFE commands (RESPOND_TO_FIRE) bypass approval
- [07-dashboard-streaming.md](07-dashboard-streaming.md) — How dashboard serves approval state via SSE
