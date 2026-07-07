# PROJECT_MAP.md — Commander_Repo / Repo-Integration Tests

## Test Files

| File | Protocol(s) | Description |
|------|-------------|-------------|
| `test_cmd_ri_001.py` | RI-CMD-001 | CentralNode publishes retained ONLINE announcement on startup |
| `test_cmd_ri_002.py` | RI-CMD-002 | HIGH severity fire → COMMAND_PENDING, no auto-dispatch |

## Dependencies

- **MQTT Broker**: Mosquitto 2.0 (Docker container `eclipse-mosquitto:2.0`)
- **Docker**: Required; tests start/stop container via `docker` CLI
- **paho-mqtt**: MQTT client for test assertions
- **Network**: Port 1883 on localhost

## Architecture

```
┌──────────────────┐     MQTT      ┌──────────────┐
│  CentralNode     │◄─────────────►│  Mosquitto   │
│  (real process)  │               │  (Docker)    │
└──────────────────┘               └──────┬───────┘
                                          │
                                   ┌──────┴───────┐
                                   │  Test Client │
                                   │  (paho-mqtt) │
                                   └──────────────┘
```

## Protocol References

- **RI-CMD-001**: Test_Protocols.md L933-950 — startup announcement
- **RI-CMD-002**: Test_Protocols.md L952-968 — HIGH fire approval gate on wire
