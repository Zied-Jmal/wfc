# PROJECT_MAP.md — Swarm_Repo / Repo-Integration Tests

## Test Files

| File | Protocol(s) | Description |
|------|-------------|-------------|
| `test_swarm_ri_001.py` | RI-SWARM-001 | SwarmLeaderNode dispatches drone command on RESPOND_TO_FIRE |
| `test_swarm_ri_002.py` | RI-SWARM-002 | OFFLINE drone excluded from FireTactics assignments (BUG 9) |

## Dependencies

- **MQTT Broker**: Mosquitto 2.0 (Docker container `eclipse-mosquitto:2.0`)
- **Docker**: Required; tests start/stop container via `docker` CLI
- **paho-mqtt**: MQTT client for test assertions
- **Network**: Port 1883 on localhost

## Architecture

```
┌──────────────────┐     MQTT      ┌──────────────┐
│ SwarmLeaderNode  │◄─────────────►│  Mosquitto   │
│ (real process)   │               │  (Docker)    │
└──────────────────┘               └──────┬───────┘
                                          │
                                   ┌──────┴───────┐
                                   │  Test Client │
                                   │  (paho-mqtt) │
                                   └──────────────┘
```

## Protocol References

- **RI-SWARM-001**: Test_Protocols.md L970-986 — drone dispatch on wire
- **RI-SWARM-002**: Test_Protocols.md L988-1008 — OFFLINE unregister regression
