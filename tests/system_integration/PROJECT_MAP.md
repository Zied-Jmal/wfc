# PROJECT_MAP.md — System-Integration Tests

## Test Files

| File | Protocol(s) | Repos | Description |
|------|-------------|-------|-------------|
| `test_si_01.py` | SI-01 | Commander + Swarm | RESPOND_TO_FIRE round trip — command reaches leader, ACK flows back |
| `test_si_02.py` | SI-02 | Swarm + Dashboard | Drone telemetry → leader → dashboard REST API |
| `test_si_03.py` | SI-03 | Commander + Swarm + Dashboard | FireEvent → command dispatch → ACK — all visible in Dashboard event log |

## Dependencies

- **MQTT Broker**: Mosquitto 2.0 (Docker container `eclipse-mosquitto:2.0`)
- **Docker**: Required; tests start/stop container via `docker` CLI
- **paho-mqtt**: MQTT client for wire-level assertions
- **requests**: HTTP client for Dashboard REST API calls
- **Network**: Ports 1883 (MQTT), 8080-8083 (Dashboard instances)

## Architecture

### SI-01: Commander ↔ Swarm
```
FireEvent ──► Commander ──MQTT──► SwarmLeader ──MQTT──► Test Subscriber
                                   │
                                   └── wfc/ack ──► Test Subscriber
```

### SI-02: Swarm ↔ Dashboard
```
Telemetry ──► SwarmLeader ──MQTT──► Dashboard ──HTTP──► Test Client
```

### SI-03: Commander ↔ Dashboard
```
FireEvent ──► Commander ──MQTT──► Dashboard ──HTTP──► Test Client
```

## Protocol References

- **SI-01**: Test_Protocols.md L1037-1055 — Commander↔Swarm round trip
- **SI-02**: Test_Protocols.md L1057-1073 — Swarm↔Dashboard telemetry
- **SI-03**: Test_Protocols.md L1075-1092 — Commander↔Dashboard approval visibility
