from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Final

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import BaseModel

from dashboard.mqtt_bridge import bridge
from dashboard.state import swarm_state
from wfc_shared.enums.command_types import (
    ABORT_MISSION,
    CONFIRM_LEADERSHIP,
    CONTAIN_FIRE,
    DISPATCH_DRONE,
    REASSIGN_LEADER,
    RECALL_DRONE,
    REINFORCE_FIRE,
    RESPOND_TO_FIRE,
    STAND_DOWN,
    UPDATE_TASK,
)
from wfc_shared.enums.events import FIRE_DETECTED
from wfc_shared.enums.topics import (
    APPROVAL_RESPONSE,
    EVENTS_FIRE,  # wfc/events/fire            - sensor trigger
    FIRE_INTENSITY,  # wfc/events/fire/intensity  - leader trigger
    command_topic,
)

MQTT_HOST: Final[str] = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT: Final[int] = int(os.getenv("MQTT_PORT", "1883"))
MQTT_WS_HOST: Final[str] = os.getenv("MQTT_WS_HOST", "localhost")
MQTT_WS_PORT: Final[int] = int(os.getenv("MQTT_WS_PORT", "9001"))
MAP_PORT: Final[int] = int(os.getenv("MAP_PORT", "8081"))


# Startup / shutdown
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    bridge.start()
    yield
    # Shutdown
    _pub.loop_stop()
    _pub.disconnect()


app = FastAPI(title="WFC Control Dashboard", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_pub = mqtt.Client(client_id="wfc-dash-pub", callback_api_version=CallbackAPIVersion.VERSION2)


def _ensure_pub() -> None:
    if not getattr(_pub, "_ok", False):
        try:
            _pub.connect(MQTT_HOST, MQTT_PORT)
            _pub.loop_start()
            _pub._ok = True  # pyright: ignore[reportAttributeAccessIssue]
        except Exception as exc:
            raise HTTPException(503, f"MQTT broker unreachable: {exc}") from exc


def _publish(topic: str, payload: dict[str, Any], qos: int = 1) -> None:
    _ensure_pub()
    _pub.publish(topic, json.dumps(payload), qos=qos)


def _command_payload(command_type: str, target_node: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "command_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "target_node": target_node,
        "command_type": command_type,
        "payload": payload,
        "timestamp": time.time(),
        "from": "ctrl-dashboard",
    }


# Config JS


@app.get("/config.js")
async def config_js() -> Response:
    js = f'window.WFC_CONFIG = {{"MQTT_WS_HOST":"{MQTT_WS_HOST}","MQTT_WS_PORT":{MQTT_WS_PORT},"MAP_PORT":{MAP_PORT}}};'
    return Response(content=js, media_type="application/javascript")


# State endpoints


@app.get("/api/nodes")
async def get_nodes() -> list[dict[str, Any]]:
    return swarm_state.get_all_nodes()


@app.get("/api/nodes/{node_id}")
async def get_node(node_id: str) -> dict[str, Any]:
    n = swarm_state.get_node(node_id)
    if not n:
        raise HTTPException(404, "Node not found")
    return n


@app.get("/api/fires")
async def get_fires() -> list[dict[str, Any]]:
    return swarm_state.get_all_fires()


@app.get("/api/events")
async def get_events(limit: int = 100) -> list[dict[str, Any]]:
    return swarm_state.get_events(limit)


@app.get("/api/elections")
async def get_elections() -> list[dict[str, Any]]:
    return swarm_state.get_election_events()


@app.get("/api/approvals")
async def get_approvals() -> list[dict[str, Any]]:
    return swarm_state.get_pending_approvals()


@app.get("/api/broker/status")
async def broker_status() -> dict[str, bool]:
    return {"connected": bridge.connected}


# Fire commands (dashboard leader)


class FireRequest(BaseModel):
    leader_id: str
    fire_id: str = ""
    location: list[float] = []  # [lat_deg, lon_deg] WGS-84


def _send_to_leader(leader_id: str, command_type: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    cmd = _command_payload(command_type, target_node=leader_id, payload=extra or {})
    _publish(command_topic(leader_id), cmd)
    swarm_state.add_event(
        {"type": "CMD_SENT", "target": leader_id, "command": command_type, "trace_id": cmd["trace_id"][:8]}
    )
    return {"ok": True, "trace_id": cmd["trace_id"]}


@app.post("/api/fire/respond")
async def fire_respond(req: FireRequest) -> dict[str, Any]:
    fire_id = req.fire_id or str(uuid.uuid4())[:8]
    return _send_to_leader(req.leader_id, RESPOND_TO_FIRE, {"fire_id": fire_id, "location": req.location})


@app.post("/api/fire/contain")
async def fire_contain(req: FireRequest) -> dict[str, Any]:
    return _send_to_leader(req.leader_id, CONTAIN_FIRE, {"fire_id": req.fire_id, "location": req.location})


@app.post("/api/fire/reinforce")
async def fire_reinforce(req: FireRequest) -> dict[str, Any]:
    return _send_to_leader(req.leader_id, REINFORCE_FIRE, {"fire_id": req.fire_id})


@app.post("/api/fire/stand_down")
async def fire_stand_down(req: FireRequest) -> dict[str, Any]:
    return _send_to_leader(req.leader_id, STAND_DOWN, {})


@app.post("/api/fire/abort")
async def fire_abort(req: FireRequest) -> dict[str, Any]:
    return _send_to_leader(req.leader_id, ABORT_MISSION, {})


# Drone commands

_VALID_DRONE_CMDS: Final[set[str]] = {DISPATCH_DRONE, RECALL_DRONE, UPDATE_TASK}


class DroneCmd(BaseModel):
    drone_id: str
    command_type: str
    payload: dict[str, Any] = {}


@app.post("/api/drone/command")
async def drone_command(req: DroneCmd) -> dict[str, Any]:
    if req.command_type not in _VALID_DRONE_CMDS:
        raise HTTPException(400, f"Invalid: {req.command_type}. Valid: {sorted(_VALID_DRONE_CMDS)}")
    cmd = _command_payload(req.command_type, target_node=req.drone_id, payload=req.payload)
    _publish(command_topic(req.drone_id), cmd)
    swarm_state.add_event(
        {"type": "CMD_SENT", "target": req.drone_id, "command": req.command_type, "trace_id": cmd["trace_id"][:8]}
    )
    return {"ok": True, "trace_id": cmd["trace_id"]}


# Leader commands

_VALID_LEADER_CMDS: Final[set[str]] = {REASSIGN_LEADER, CONFIRM_LEADERSHIP}


class LeaderCmd(BaseModel):
    leader_id: str
    command_type: str
    payload: dict[str, Any] = {}


@app.post("/api/leader/command")
async def leader_command(req: LeaderCmd) -> dict[str, Any]:
    if req.command_type not in _VALID_LEADER_CMDS:
        raise HTTPException(400, f"Invalid: {req.command_type}")
    return _send_to_leader(req.leader_id, req.command_type, req.payload)


# Generic command


class GenericCmd(BaseModel):
    target_node_id: str
    command_type: str
    payload: dict[str, Any] = {}


@app.post("/api/command")
async def generic_command(req: GenericCmd) -> dict[str, Any]:
    return _send_to_leader(req.target_node_id, req.command_type, req.payload)


# Approval response


class ApprovalResp(BaseModel):
    request_id: str
    approved: bool
    reason: str = ""


@app.post("/api/approval/respond")
async def approval_respond(req: ApprovalResp) -> dict[str, Any]:
    # Wire contract: decision must be "APPROVED" or "REJECTED" string (not a bool).
    # CommanderCore reads decision, not approved - sending a bool silently broke approvals.
    decision = "APPROVED" if req.approved else "REJECTED"
    _publish(
        APPROVAL_RESPONSE,
        {
            "pending_id": req.request_id,
            "decision": decision,
            "operator_id": "ctrl-dashboard",
            "reason": req.reason,
            "timestamp": time.time(),
        },
    )
    swarm_state.add_event({"type": "APPROVAL_RESP", "request_id": req.request_id[:8], "approved": req.approved})
    return {"ok": True}


# Fire injection - two triggers


class InjectFireSensor(BaseModel):
    """Sensor trigger - publishes FireEvent to wfc/events/fire.
    Use this to START a new fire."""

    fire_id: str = ""
    zone: str = "zone_alpha"
    severity: str = "MEDIUM"  # LOW|MEDIUM|HIGH|CRITICAL
    sensor_id: str = "sim-sensor-01"
    location_coords: list[float] = []  # [lat_deg, lon_deg] WGS-84


@app.post("/api/inject/fire/sensor")
async def inject_fire_sensor(req: InjectFireSensor) -> dict[str, Any]:
    """Simulate a ground sensor detecting fire (FIRE_DETECTED event).
    Published to wfc/events/fire - triggers CommanderCore._handle_fire_event()
    rule engine FireDispatchRule RESPOND_TO_FIRE to nearest leader.
    """
    fire_id = req.fire_id or str(uuid.uuid4())[:8]
    event_payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": FIRE_DETECTED,
        "timestamp": time.time(),
        "source": req.sensor_id,
        "payload": {
            "fire_id": fire_id,
            "zone": req.zone,
            "severity": req.severity,
            "sensor_id": req.sensor_id,
            "location_coords": req.location_coords if len(req.location_coords) == 2 else None,
        },
    }
    _publish(EVENTS_FIRE, event_payload, qos=1)
    swarm_state.add_event(
        {
            "type": "FIRE_SENSOR_INJECT",
            "fire_id": fire_id,
            "zone": req.zone,
            "severity": req.severity,
        }
    )
    return {"ok": True, "fire_id": fire_id, "trigger": "sensor", "topic": EVENTS_FIRE}


class InjectFireIntensity(BaseModel):
    """Leader trigger - publishes FireIntensityUpdate to wfc/events/fire/intensity.
    Use this to ESCALATE/DE-ESCALATE an existing fire, not to start one."""

    fire_id: str = ""
    leader_id: str = "sim-leader-01"
    new_intensity: str = "MEDIUM"  # LOW|MEDIUM|HIGH|CRITICAL
    perimeter_m: float = 0.0  # m
    spread_rate: str = "SLOW"  # SLOW|MODERATE|RAPID
    wind_speed_mps: float = 0.0  # m/s


@app.post("/api/inject/fire/intensity")
async def inject_fire_intensity(req: InjectFireIntensity) -> dict[str, Any]:
    """Simulate a leader reporting fire intensity change (FireIntensityUpdate).
    Published to wfc/events/fire/intensity - triggers severity/expansion rules.
    """
    fire_id = req.fire_id or str(uuid.uuid4())[:8]
    payload = {
        "fire_id": fire_id,
        "leader_id": req.leader_id,
        "new_intensity": req.new_intensity,
        "perimeter_m": req.perimeter_m,
        "spread_rate": req.spread_rate,
        "wind_speed_mps": req.wind_speed_mps,
        "timestamp": time.time(),
    }
    _publish(FIRE_INTENSITY, payload, qos=1)
    swarm_state.add_event(
        {
            "type": "FIRE_INTENSITY_INJECT",
            "fire_id": fire_id,
            "leader_id": req.leader_id,
            "intensity": req.new_intensity,
        }
    )
    return {"ok": True, "fire_id": fire_id, "trigger": "leader_intensity", "topic": FIRE_INTENSITY}


# Raw MQTT inject (testing)


class InjectRaw(BaseModel):
    topic: str
    payload: dict[str, Any] = {}


@app.post("/api/inject/raw")
async def inject_raw(req: InjectRaw) -> dict[str, Any]:
    _publish(req.topic, {**req.payload, "timestamp": time.time()}, qos=0)
    swarm_state.add_event({"type": "RAW_PUBLISH", "topic": req.topic})
    return {"ok": True}


# SSE stream


@app.get("/api/stream")
async def sse_stream(request: Request) -> StreamingResponse:
    async def generator() -> AsyncGenerator[str]:
        last_count = 0
        while True:
            if await request.is_disconnected():
                break
            events = swarm_state.get_events(50)
            new_events = events[last_count:] if last_count < len(events) else []
            last_count = len(events)
            data = json.dumps(
                {
                    "nodes": swarm_state.get_all_nodes(),
                    "fires": swarm_state.get_all_fires(),
                    "new_events": new_events,
                    "ts": time.time(),
                }
            )
            yield f"data: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
