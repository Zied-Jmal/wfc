# Commander_Repo

Central orchestration node for the WFC system. Runs the 15-rule fire evaluation engine, manages the approval gate for high-risk commands, tracks node heartbeats, and syncs state between primary and backup commanders.

## Run

```bash
# Local
python -m command_nodes.central.main

# Docker
docker build -t wfc-commander .
docker run --env-file .env wfc-commander
```

## Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --timeout=30
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `commander_core.py` | God class — connects MQTT, routes all messages |
| `rule_engine/` | 15 rules evaluating fire events against node state |
| `approval/` | High-risk command gating + operator response routing |
| `heartbeat/` | Node liveness tracking via `last_seen_at` timestamps |
| `state/` | Node registry, fire state, mission store, snapshots |
| `repositories/` | SQLite persistence for nodes, missions, events |

## Adding a New Rule

1. Create `core/rule_engine/rules/your_rule.py`
2. Subclass `Rule` with `name`, `description`, `priority`
3. Implement `evaluate(context: RuleContext) -> bool`
4. Return `True` to fire, `False` to skip
5. Register in `rules/__init__.py`
