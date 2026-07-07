# PROJECT_MAP.md — Integration_Tests / E2E Scenarios

## Pre-existing (already implemented)

These 5 scenarios were pre-existing in `Integration_Tests/orchestrator/scenarios/`. They run against the full Docker Compose stack via the orchestrator web UI at `http://localhost:9090`.

| File | Scenario | Stages | Description |
|------|----------|--------|-------------|
| `scenario_1_fire_dispatch.py` | E2E-01 | A–F (6) | Sensor → Commander → Leader → Drone → Telemetry |
| `scenario_2_telemetry_aggregation.py` | E2E-02 | 4 | Synthetic telemetry → Leader snapshot → Dashboard |
| `scenario_3_leader_election.py` | E2E-03 | 5 | Bully election failover on leader death |
| `scenario_4_approval_gate.py` | E2E-04 | 5 | High-risk command → ApprovalGate → Operator approves |
| `scenario_5_node_lifecycle.py` | E2E-05 | 5 | LWT crash detection on ungraceful disconnect |

## How to Run

```bash
cd Infrastructure_Repo
docker compose up --build
# Then open http://localhost:9090
```

## Protocol References

- **E2E-01**: Test_Protocols.md L1104-1120 — Fire Dispatch End-to-End
- **E2E-02**: Test_Protocols.md L1122-1128 — Telemetry Aggregation
- **E2E-03**: Test_Protocols.md L1130-1140 — Leader Election Failover
- **E2E-04**: Test_Protocols.md L1142-1154 — Human Approval Gate
- **E2E-05**: Test_Protocols.md L1156-1164 — Node Lifecycle & LWT
