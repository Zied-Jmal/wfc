# Swarm_Repo

Drone node implementations for the WFC system. Contains scout drones, firefighting drones, and swarm leaders with tactical coordination, leader election (bully protocol), and a full physics engine.

## Run

```bash
# Swarm Leader
python main.py --node-id sl-A-01 --node-zone zone_alpha

# Scout Drone
python main.py --node-id sd-A-01 --node-zone zone_alpha

# Firefighting Drone
python main.py --node-id fd-A-01 --node-zone zone_alpha
```

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --timeout=30
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `core/swarm_leader_node.py` | Tactical commander — manages drones in a zone |
| `core/leader_election.py` | Bully protocol — detects leader death, elects replacement |
| `core/action/` | GPS, suppression, scouting, refueling, wind effects |
| `core/physics/` | Movement, sensors, resources, environment model |
| `core/telemetry/` | Aggregation, format, pipeline |
| `core/heartbeat/` | Periodic heartbeat publishing |
| `config.py` | Node identity, zone, capabilities from env vars |

## Drone Types

| Type | Role |
|------|------|
| `ScoutDroneNode` | Surveys fire, reports telemetry, returns to base |
| `FirefightingDroneNode` | Suppresses fire with water payload, refuels at base |
| `SwarmLeaderNode` | Coordinates drones, runs fire tactics, runs for election |
