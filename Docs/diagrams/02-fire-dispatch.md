# Fire Dispatch (G-05)

Covers: FireEvent → Commander RuleEngine → Swarm Leader → Drone assignment + ACK protocol.

References: [01-node-lifecycle](01-node-lifecycle.md) for node readiness; [03-telemetry-pipeline](03-telemetry-pipeline.md) for post-dispatch telemetry; [05-approval-gate](05-approval-gate.md) for high-risk variants.

```mermaid
sequenceDiagram
  participant Sensor as Ground Sensor<br/>(Test Harness)
  participant MQTT as Mosquitto<br/>MQTT Broker
  participant Commander as CommanderCore<br/>(central-commander)
  participant RE as RuleEngine
  participant NR as NodeRegistry
  participant Leader as SwarmLeaderNode<br/>(sl-A-01)
  participant FT as FireTactics
  participant SD as Scout Drone<br/>(sd-A-01)
  participant FD as Firefighting Drone<br/>(fd-A-01)
  participant Dash as Dashboard<br/>SwarmState

  Note over Sensor,Dash: --- PREREQUISITE: Node Readiness (see 01-node-lifecycle) ---
  Note over NR: 1. Commander, Leader, Scouts, Fighters<br/>all registered + ACTIVE

  rect rgb(220, 240, 220)
    Note over Sensor,Dash: STAGE A: RULE ENGINE DISPATCH
    Sensor->>MQTT: publish<br/>topic: wfc/events/fire<br/>payload: FireEvent( fire_id, zone="zone_alpha",<br/>  severity="HIGH", location_coords=[36.8,10.18] )
    MQTT-->>Commander: deliver
    Commander->>RE: evaluate(fire, context)
    RE->>NR: get_available(capability=SWARM_LEAD)
    NR-->>RE: [sl-A-01]
    RE->>RE: rule 'fire_dispatch' fires<br/>command_type=RESPOND_TO_FIRE<br/>risk=SAFE → requires_approval=False
    RE->>Commander: dispatch RESPOND_TO_FIRE → sl-A-01
    Commander->>MQTT: publish<br/>topic: wfc/command/sl-A-01<br/>payload: { command_type: "RESPOND_TO_FIRE",<br/>  trace_id, target_node: "sl-A-01",<br/>  payload: { fire_id, severity, location_coords } }
  end

  rect rgb(240, 240, 255)
    Note over Sensor,Dash: STAGE B: LEADER ACK RECEIVED
    MQTT-->>Leader: deliver RESPOND_TO_FIRE
    Leader->>Leader: _handle_command()<br/>trace_id not in _handled_trace_ids
    Leader->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "sl-A-01", status: "RECEIVED" }
  end

  rect rgb(255, 240, 220)
    Note over Sensor,Dash: STAGE C: TACTICS + DISPATCH
    Leader->>Leader: _execute_command() → _cmd_respond_to_fire()
    Leader->>Leader: set _current_fire_id, severity
    Leader->>FT: assign_respond_to_fire(fire_id, fire_pos, severity, scouts, fighters)
    FT-->>Leader: [ DroneAssignment(sd-A-01, SCOUTING),<br/>  DroneAssignment(fd-A-01, SUPPRESSING) ]
    Leader->>Leader: _dispatch_assignments()
    Leader->>MQTT: publish<br/>topic: wfc/command/sd-A-01<br/>payload: { command_type: "UPDATE_TASK",<br/>  target_node: "sd-A-01", task: "SCOUTING", target_pos: [...] }
    Leader->>MQTT: publish<br/>topic: wfc/command/fd-A-01<br/>payload: { command_type: "UPDATE_TASK",<br/>  target_node: "fd-A-01", task: "SUPPRESSING", target_pos: [...] }
    Leader->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "sl-A-01", status: "EXECUTED" }
    MQTT-->>Commander: ACK EXECUTED received
    MQTT-->>Dash: ACK EXECUTED logged
  end

  rect rgb(220, 255, 220)
    Note over Sensor,Dash: STAGE D+E: DRONE COMMAND + ACK
    MQTT-->>SD: deliver UPDATE_TASK
    SD->>SD: _handle_command()
    SD->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "sd-A-01", status: "RECEIVED" }
    SD->>SD: _execute_command() → task=SCOUTING
    SD->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "sd-A-01", status: "EXECUTED" }

    MQTT-->>FD: deliver UPDATE_TASK
    FD->>FD: _handle_command()
    FD->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "fd-A-01", status: "RECEIVED" }
    FD->>FD: _execute_command() → task=SUPPRESSING
    FD->>MQTT: publish<br/>topic: wfc/ack<br/>payload: { trace_id, node_id: "fd-A-01", status: "EXECUTED" }
  end

  rect rgb(240, 240, 240)
    Note over Sensor,Dash: STAGE F: TELEMETRY FLOW (see 03-telemetry-pipeline)
    SD->>MQTT: publish (every 2s)<br/>topic: wfc/telemetry/sd-A-01<br/>payload: DroneTelemetry( leader_id="sl-A-01", ... )
    FD->>MQTT: publish (every 2s)<br/>topic: wfc/telemetry/fd-A-01<br/>payload: DroneTelemetry( leader_id="sl-A-01", ... )
    MQTT-->>Leader: ingest telemetry
    MQTT-->>Dash: ingest telemetry
  end
```

## Command Risk & Approval Gate

| Command Type | Risk Level | Requires Approval | Behavior |
|---|---|---|---|
| `RESPOND_TO_FIRE` | SAFE | No | Auto-dispatched (shown above) |
| `CONTAIN_FIRE` | SAFE | No | Auto-dispatched |
| `STAND_DOWN` | SAFE | No | Auto-dispatched |
| `REINFORCE_FIRE` | SAFE | No | Auto-dispatched |
| `PREEMPT_RESOURCE` | DISRUPTIVE | Yes | [See approval-gate](05-approval-gate.md) |
| `ESCALATE_FIRE` | IRREVERSIBLE | Yes | [See approval-gate](05-approval-gate.md) |
| `ABORT_MISSION` | IRREVERSIBLE | Yes | [See approval-gate](05-approval-gate.md) |
| `OVERRIDE_SAFETY` | IRREVERSIBLE | Yes | [See approval-gate](05-approval-gate.md) |

## ACK Protocol (FieldNode base class)

```
_handle_command(payload)
  1. Dedup check (trace_id in _handled_trace_ids → re-ACK EXECUTED, return)
  2. send_ack(RECEIVED)
  3. Validate command_type
  4. _execute_command(command_type, fire_payload, trace_id)  ← subclass implements
  5. _handled_trace_ids.add(trace_id)
  6. send_ack(EXECUTED)
```

## Key File Paths

| File | Role |
|---|---|
| `Commander_Repo/command_nodes/core_commander/commander_core.py:779` | `_handle_fire_event()` → rule evaluation → dispatch |
| `Commander_Repo/core/rule_engine/engine.py` | `evaluate()`, risk lookup |
| `Swarm_Repo/core/node/field_node.py:136` | `_handle_command()` — base ACK protocol |
| `Swarm_Repo/core/node/swarm_leader_node.py:248` | `_execute_command()` → dispatches to `_cmd_respond_to_fire()` |
| `Swarm_Repo/core/node/swarm_leader_node.py:292` | `_cmd_respond_to_fire()` → tactics → dispatch |
| `Swarm_Repo/core/tactics/fire_tactics.py:58` | `assign_respond_to_fire()` |
| `Swarm_Repo/core/node/swarm_leader_node.py:346` | `_dispatch_assignments()` → per-drone UPDATE_TASK |

## See also

- [01-node-lifecycle.md](01-node-lifecycle.md) — Prerequisite: all nodes must be ACTIVE
- [03-telemetry-pipeline.md](03-telemetry-pipeline.md) — Post-dispatch telemetry flow
- [05-approval-gate.md](05-approval-gate.md) — High-risk command variant
