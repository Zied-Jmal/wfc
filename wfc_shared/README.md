# wfc_shared

Single source of truth for all WFC system contracts. Defines the schemas, enums, and topic constants shared across Commander, Swarm, Dashboard, and Integration Tests.

## Install

```bash
pip install -e ./wfc_shared
```

## What's Inside

```
wfc_shared/
  schemas/      Pydantic models (NodeAnnouncement, Command, FireEvent, DroneTelemetry, etc.)
  enums/        Command types, fire status, node status, node types, topic builders
  topics.py     MQTT topic builder functions
  transitions.py  Fire + mission state machine definitions
```

## Usage

```python
from wfc_shared.schemas.nodes import NodeAnnouncement
from wfc_shared.enums.command_types import SWARM_LEAD
from wfc_shared.enums.topics import registry_announce_topic

# Build a topic
topic = registry_announce_topic("sl-A-01")  # "wfc/registry/announce/sl-A-01"

# Validate a message
node = NodeAnnouncement(**mqtt_payload)
```

## Type Checking

This package ships a `py.typed` marker. Type checkers (Pyright, mypy) will use the inline type annotations automatically.
