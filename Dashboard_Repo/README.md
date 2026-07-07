# Dashboard_Repo

Web dashboard for the WFC system. Provides a real-time map view, REST API, and SSE streaming of all system events.

## Run

```bash
python run.py
# or
uvicorn dashboard.server:app --host 0.0.0.0 --port 8080
```

Dashboard: http://localhost:8080
Map view: http://localhost:8081

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --timeout=30
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/broker/status` | MQTT broker connection status |
| GET | `/api/nodes` | All registered nodes |
| GET | `/api/nodes/{id}` | Single node detail |
| GET | `/api/fires` | All active fires |
| GET | `/api/missions` | All missions |
| GET | `/api/approvals` | Pending approvals |
| POST | `/api/approval/{id}/respond` | Approve/reject a command |
| GET | `/api/stream` | SSE event stream |

## Key Modules

| Module | Purpose |
|--------|---------|
| `server.py` | FastAPI app, REST endpoints, SSE |
| `mqtt_bridge.py` | Subscribes to `wfc/#`, routes to SwarmState |
| `state.py` | In-memory state machine — nodes, fires, events |
| `map_server.py` | Static file server for map view on port 8081 |
