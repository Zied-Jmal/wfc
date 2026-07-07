# PROJECT_MAP.md — Dashboard_Repo / Repo-Integration Tests

## Test Files

| File | Protocol(s) | Description |
|------|-------------|-------------|
| `test_dash_ri_001.py` | RI-DASH-001 | POST /api/approval/respond publishes `decision: "APPROVED"` (not `approved: true`) |

## Dependencies

- **MQTT Broker**: Mosquitto 2.0 (Docker container `eclipse-mosquitto:2.0`)
- **Docker**: Required; tests start/stop container via `docker` CLI
- **paho-mqtt**: MQTT client for test assertions
- **FastAPI / uvicorn**: Dashboard web server
- **Network**: Ports 1883 (MQTT), 8080 (Dashboard)

## Architecture

```
┌──────────────────┐     HTTP      ┌──────────────┐
│  Test Client     │──────────────►│  Dashboard   │
│  (requests)      │ POST /api/    │  (uvicorn)   │
└──────────────────┘               └──────┬───────┘
                                          │ MQTT
                                   ┌──────┴───────┐
                                   │  Mosquitto   │
                                   │  (Docker)    │
                                   └──────┬───────┘
                                          │
                                   ┌──────┴───────┐
                                   │  Test Client │
                                   │  (paho-mqtt) │
                                   └──────────────┘
```

## Protocol References

- **RI-DASH-001**: Test_Protocols.md L1010-1028 — approval wire format regression
