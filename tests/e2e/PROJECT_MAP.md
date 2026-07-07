# PROJECT_MAP.md — E2E Scenarios (subprocess-based)

## Test Files

| File | Scenario | Repos | Description |
|------|----------|-------|-------------|
| `test_e2e_01.py` | E2E-01 | Commander + Swarm | FireEvent → RESPOND_TO_FIRE command → RECEIVED ACK |
| `test_e2e_03.py` | E2E-03 | 2× Swarm Leaders | Kill active leader → backup detects timeout → re-announces as SWARM_LEAD |
| `test_e2e_05.py` | E2E-05 | Swarm Leader | Start node → verify ONLINE → graceful shutdown → verify OFFLINE published |

## Key Fixtures (conftest.py)

- `central_process` — CentralNode subprocess (Commander_Repo)
- `swarm_leader_process` — active SwarmLeaderNode subprocess (Swarm_Repo)
- `backup_leader_process` — backup SwarmLeaderNode subprocess with heartbeat monitoring
- `scout_drone_process` — ScoutDroneNode subprocess (Swarm_Repo)
- `dashboard_process` — Dashboard subprocess (Dashboard_Repo)

## Architecture

Each node runs as an isolated Python subprocess with only its own repo on `PYTHONPATH`. Tests communicate via MQTT (paho-mqtt) and HTTP (requests for Dashboard API).

## Protocol References

- **E2E-03**: Test_Protocols.md L1130-1140 — Leader Election Failover
- **E2E-05**: Test_Protocols.md L1156-1164 — Node Lifecycle & LWT
