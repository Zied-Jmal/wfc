# Integration_Tests

End-to-end test orchestrator for the WFC system. Spins up simulated nodes, publishes events, and verifies system behavior through a web UI with live pipeline streaming.

## Run

```bash
python run.py
# or
uvicorn orchestrator.app:app --host 0.0.0.0 --port 9090
```

Test console: http://localhost:9090

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --timeout=60
```

## Test Scenarios

| Scenario | What It Tests |
|----------|---------------|
| Scenario 1 | Basic fire detection and drone dispatch |
| Scenario 2 | Multi-zone fire response |
| Scenario 3 | Telemetry pipeline integrity |
| Scenario 4 | Approval gate for high-risk commands |
| Scenario 5 | Leader election failover |

## Key Modules

| Module | Purpose |
|--------|---------|
| `orchestrator/app.py` | FastAPI app, scenario management, SSE streaming |
| `orchestrator/scenarios/` | Individual test scenario implementations |
| `orchestrator/simulation/` | Simulated ground sensor, drone nodes |
