# WFC Swarm System — Quick Start Guide
**Version 2.0 | Physical Engine + ISO Units + Real GPS**

---

## What Is This System?

Three repos that talk to each other over **MQTT**:

```
┌──────────────────┐     MQTT      ┌──────────────────┐     MQTT      ┌──────────────────┐
│   wfc_main       │ ◄──────────── │   swarm_repo     │ ──────────── │   dashboard      │
│  (commander,     │               │  (leader,scouts, │               │  (FastAPI UI,    │
│   rule engine,   │               │   firefighters,  │               │   map, inject,   │
│   frozen ✓)      │               │   action engine) │               │   SSE stream)    │
└──────────────────┘               └──────────────────┘               └──────────────────┘
```

**Rule**: wfc_main is the source of truth. swarm_repo respects it. Dashboard respects both.

---

## 1. Prerequisites

```bash
# Required
docker --version        # Docker 24+
docker compose version  # Compose v2+

# Broker included in compose — no separate install needed
```

---

## 2. Folder Structure After Applying Patches

```
your_workspace/
├── wfc_main/               ← frozen, do not modify
├── swarm_repo/
│   ├── action/             ← NEW: physical engine (GPS, wind, sensors)
│   │   ├── gps.py          ← WGS-84 math, Haversine, NED frame
│   │   ├── wind.py         ← Dryden turbulence + fire plume
│   │   ├── movement.py     ← 3-D kinematics, collision avoidance
│   │   ├── sensors.py      ← thermal (Stefan-Boltzmann), smoke (Gaussian plume)
│   │   ├── resources.py    ← Wh battery, litre payload, FSPL connectivity
│   │   ├── scouting.py     ← orbit, grid sweep, sensor coupling
│   │   └── suppression.py  ← approach/drop/egress, wind-corrected
│   ├── core/node/
│   │   ├── scout_drone_node.py       ← updated: uses action/
│   │   └── firefighting_drone_node.py← updated: uses action/
│   ├── core/utils/config.py          ← updated: GPS, wind, payload env vars
│   ├── main_scout.py                 ← updated: GPSCoord + WindModel
│   ├── main_fighter.py               ← updated: litres payload + WindModel
│   ├── main_leader.py                ← updated: WGS-84 location
│   └── docker-compose.yml            ← updated: real GPS coords + new env vars
└── dashboard/
    ├── dashboard/
    │   ├── server.py       ← two fire triggers, GPS inject forms
    │   ├── mqtt_bridge.py  ← EVENTS_FIRE constant, V2 telemetry parsing
    │   ├── state.py        ← all ISO fields, GPS position
    │   └── templates/
    │       ├── dashboard.html  ← V2 ISO columns, two inject panels
    │       └── map.html        ← real Mercator GPS map
    └── shared/schemas/
        └── telemetry.py    ← V2 ISO fields (battery_wh, payload_litres, etc.)
```

---

## 3. Start the Full Stack

```bash
cd swarm_repo

# First time — build all images
docker compose up --build

# After that — just start
docker compose up

# Run in background
docker compose up -d
```

**Wait ~10 seconds** for broker + nodes to connect. You'll see:

```
sl-A-01  | [sl-A-01] LEADER (ACTIVE) started
sd-A-01  | [sd-A-01] SCOUT started
fd-A-01  | [fd-A-01] FIREFIGHTER started  payload=10.0 L water
```

---

## 4. Access the Dashboard

| URL | What |
|-----|------|
| `http://localhost:8080` | Main dashboard |
| `http://localhost:8080/map` | Live GPS map |
| `http://localhost:8080/api/nodes` | Raw node JSON |
| `http://localhost:8080/api/fires` | Active fires JSON |
| `http://localhost:8080/api/stream` | SSE live stream |
| `localhost:1883` | MQTT broker (TCP) |
| `localhost:9001` | MQTT broker (WebSocket) |

---

## 5. Simulate a Fire — Two Ways

### Way 1: Sensor detects new fire (primary path)

```bash
curl -X POST http://localhost:8080/api/inject/fire/sensor \
  -H "Content-Type: application/json" \
  -d '{
    "fire_id":         "fire-001",
    "zone":            "zone_alpha",
    "severity":        "HIGH",
    "sensor_id":       "sensor-ground-01",
    "location_coords": [36.8165, 10.2015]
  }'
```

**What happens:**
1. Dashboard publishes `FireEvent` → `wfc/events/fire`
2. Commander's `_handle_fire_event()` fires → rule engine runs
3. `FireDispatchRule` finds nearest leader → sends `RESPOND_TO_FIRE`
4. Leader dispatches scouts + firefighters

### Way 2: Leader reports intensity change (escalation path)

```bash
curl -X POST http://localhost:8080/api/inject/fire/intensity \
  -H "Content-Type: application/json" \
  -d '{
    "fire_id":        "fire-001",
    "leader_id":      "sl-A-01",
    "new_intensity":  "CRITICAL",
    "perimeter_m":    350.0,
    "spread_rate":    "RAPID",
    "wind_speed_mps": 8.5
  }'
```

**Use this for:** escalating an existing fire, not starting a new one.

---

## 6. Common Dashboard Actions

| Action | Where |
|--------|-------|
| Start a fire | **Inject** page → "Sensor Trigger" panel |
| Escalate a fire | **Inject** page → "Leader Trigger" panel |
| Send RESPOND_TO_FIRE to leader | **Fire Ops** page → fire command panel |
| Manually dispatch a drone | **Drone Control** page → command panel |
| Approve a pending command | **Commanders** page → approval list |
| Watch live positions | **Map** (`http://localhost:8080/map`) |
| Follow event log | **Events** page or bottom of any page |

---

## 7. Map Controls

| Control | Action |
|---------|--------|
| **Drag** | Pan map |
| **Scroll wheel** | Zoom in/out (anchors at cursor position) |
| **Click node in sidebar** | Centre map on that node |
| **Reset View button** | Auto-fit all nodes on screen |
| **Pan to Lat/Lon inputs** | Jump to any WGS-84 coordinate |
| **Cursor** | Shows live lat/lon in bottom bar |

**Legend:**
- 🟡 Yellow circle = Swarm Leader
- 🔵 Cyan circle = Scout Drone
- 🔴 Red circle = Firefighting Drone
- 🟣 Purple circle = Commander
- 🟡 Pulsing ring = Pump active (suppression in progress)
- 🔥 Coloured zone = Fire (radius from perimeter estimate)
- Dashed line = Flight trail

---

## 8. Drone Telemetry Fields (ISO Units)

| Field | Unit | Description |
|-------|------|-------------|
| `position` | °N, °E | WGS-84 decimal degrees |
| `altitude_m_amsl` | m | Altitude above mean sea level |
| `battery_wh` | Wh | Energy remaining (TB60 = 585.9 Wh full) |
| `battery_pct` | 0.0–1.0 | Fraction of full charge |
| `payload_litres` | L | Suppressant remaining (10 L full tank) |
| `payload_kg` | kg | Mass of remaining suppressant |
| `thermal_peak_temp_c` | °C | Hottest FLIR pixel (Stefan-Boltzmann model) |
| `smoke_density_mg_m3` | mg/m³ | Particulate concentration (Gaussian plume) |
| `flame_height_m` | m | Flame height (Heskestad correlation) |
| `wind_speed_mps` | m/s | Anemometer reading |
| `wind_direction_deg` | °T | FROM direction, meteorological |
| `distance_to_flame_m` | m | Laser rangefinder slant range |
| `perimeter_estimate_m` | m | Fire perimeter after orbit pass |
| `litres_delivered` | L | Total suppressant dropped on fire |
| `suppression_effectiveness_pct` | 0.0–1.0 | L delivered vs L needed for fire area |
| `drop_passes` | int | Number of completed drop runs |
| `connectivity` | STRONG/WEAK/LOST | FSPL model at 900 MHz |

---

## 9. Useful Docker Commands

```bash
# Stop everything
docker compose down

# Stop everything AND delete volumes (fresh start)
docker compose down -v

# Restart a single drone
docker compose restart fd-A-01

# Kill the active leader (triggers election)
docker compose stop sl-A-01

# Follow logs for one node
docker compose logs -f sd-A-01

# Rebuild just the dashboard
docker compose up --build dashboard

# Scale: add a 4th firefighter on the fly
docker compose run --rm \
  -e NODE_ID=fd-A-04 \
  -e NODE_LOCATION="36.8090,10.1810" \
  -e LEADER_ID=sl-A-01 \
  fd-A-01
```

---

## 10. Environment Variables Reference

All have safe defaults. Override in `docker-compose.yml` or `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `NODE_ID` | `swarm-node-01` | Unique node identifier |
| `NODE_ZONE` | `zone_alpha` | Operational zone |
| `NODE_LOCATION` | `36.8065,10.1815` | Home GPS `lat,lon` WGS-84 |
| `HOME_ALT_M` | `50.0` | Home altitude m AMSL |
| `LEADER_ID` | `sl-A-01` | Parent leader (drones only) |
| `WIND_SPEED_MPS` | `5.0` | Initial wind speed m/s |
| `WIND_DIR_DEG` | `225.0` | Wind FROM direction °T |
| `TURBULENCE` | `LIGHT` | `NONE/LIGHT/MODERATE/SEVERE` |
| `PAYLOAD_TYPE` | `water` | `water` or `retardant` |
| `INITIAL_PAYLOAD_L` | `10.0` | Starting payload litres |
| `INITIAL_BATTERY_WH` | `585.9` | Starting battery Wh |
| `MQTT_HOST` | `localhost` | Broker hostname |
| `MQTT_PORT` | `1883` | Broker TCP port |

---

## 11. MQTT Topics Cheat Sheet

| Topic | Direction | Schema | Purpose |
|-------|-----------|--------|---------|
| `wfc/events/fire` | sensor → commander | `FireEvent` | New fire detected |
| `wfc/events/fire/intensity` | leader → commander | `FireIntensityUpdate` | Intensity change |
| `wfc/registry/announce/{id}` | node → all | `NodeAnnouncement` | Node joins/leaves |
| `wfc/telemetry/{drone_id}` | drone → leader | `DroneTelemetry` | Live telemetry (2s) |
| `wfc/swarm/status/{leader_id}` | leader → commander | `SwarmStatusSnapshot` | Fleet summary (10s) |
| `wfc/command/{node_id}` | commander/leader → node | `Command` | Dispatch/recall/etc. |
| `wfc/swarm/election/{zone}` | leader → all | election payload | Election result |
| `wfc/ack` | node → all | ack payload | Command acknowledged |
| `wfc/approval/pending` | commander → dashboard | `PendingCommand` | Human approval needed |
| `wfc/approval/response` | dashboard → commander | approval payload | Approve/reject |
| `wfc/state/snapshot` | commander → all | snapshot | Commander heartbeat |

---

## 12. Troubleshooting

**Drones not appearing on map**
→ Check `NODE_LOCATION` format: must be `"lat,lon"` e.g. `"36.8065,10.1815"` not `"x,y"`
→ Dashboard map auto-fits on first data — wait 10s after startup

**Fire inject returns 503**
→ MQTT broker not reachable — check `docker compose ps broker`
→ Wait for `service_healthy` on broker before retrying

**Leader election looping**
→ Normal if `sl-A-01` is stopped — `sl-A-02` or `sl-A-03` takes over within 5s
→ Restart all leaders: `docker compose restart sl-A-01 sl-A-02 sl-A-03`

**Battery draining too fast in tests**
→ Set `INITIAL_BATTERY_WH=5859.0` (10× real) to slow drain for long demos

**Payload empty instantly**
→ 10L tank empties in ~20s at 0.5 L/s — expected behaviour
→ Drone returns to base, reloads, and re-dispatches automatically

**Map shows wrong area**
→ Click "Reset View" — auto-fits to all node GPS positions
→ Or type target lat/lon in the "Pan to" inputs

**Dashboard shows stale data (yellow badge)**
→ Node has not published telemetry in >15s — check `docker compose logs <node_id>`

---

## 13. Architecture Quick Reference

```
Fire detected
     │
     ▼
wfc/events/fire  ──►  Commander (wfc_main)
                            │
                     Rule Engine runs
                            │
                     RESPOND_TO_FIRE ──► Swarm Leader (sl-A-01)
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                        DISPATCH_DRONE   DISPATCH_DRONE   DISPATCH_DRONE
                              │                │                │
                         Scout (sd-A-01)  Fighter (fd-A-01)  Fighter (fd-A-02)
                              │                │                │
                       orbit fire          approach         approach
                       read sensors        drop 10L         drop 10L
                              │                │                │
                       DroneTelemetry    DroneTelemetry    DroneTelemetry
                       (every 2s)        (every 2s)        (every 2s)
                              │
                       SwarmStatusSnapshot (every 10s)
                              │
                       Dashboard SSE stream ──► Browser
```
