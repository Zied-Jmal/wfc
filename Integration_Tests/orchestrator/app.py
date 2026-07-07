# orchestrator/app.py
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Final

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.engine import Scenario, ScenarioRunner, RunReport
from orchestrator.mqtt_bus import MQTTBus

from orchestrator.scenarios.scenario_1_fire_dispatch import build as build_s1
from orchestrator.scenarios.scenario_2_telemetry_aggregation import build as build_s2
from orchestrator.scenarios.scenario_3_leader_election import build as build_s3
from orchestrator.scenarios.scenario_4_approval_gate import build as build_s4
from orchestrator.scenarios.scenario_5_node_lifecycle import build as build_s5


app: Final[FastAPI] = FastAPI(title="WFC Integration Test Orchestrator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bus: Final[MQTTBus] = MQTTBus()

SCENARIO_BUILDERS: Final[dict[str, Any]] = {
    "fire_dispatch":          build_s1,
    "telemetry_aggregation":  build_s2,
    "leader_election":        build_s3,
    "approval_gate":          build_s4,
    "node_lifecycle":         build_s5,
}

SCENARIO_META: Final[list[dict[str, str]]] = [
    {"id": "fire_dispatch", "title": "1. Fire Dispatch End-to-End",
     "summary": "Sensor \u2192 Commander \u2192 Leader \u2192 Drone \u2192 Telemetry. The flagship flow."},
    {"id": "telemetry_aggregation", "title": "2. Telemetry Aggregation & Dashboard",
     "summary": "Synthetic drone telemetry \u2192 Leader snapshot \u2192 Dashboard REST/SSE."},
    {"id": "leader_election", "title": "3. Leader Election Failover",
     "summary": "Simulate leader death \u2192 Bully election \u2192 New leader resumes ops."},
    {"id": "approval_gate", "title": "4. Human Approval Gate",
     "summary": "High-risk command \u2192 ApprovalGate \u2192 Operator approves \u2192 Forwarded."},
    {"id": "node_lifecycle", "title": "5. Node Lifecycle & LWT Crash Detection",
     "summary": "Fake node joins, heartbeats, crashes ungracefully \u2192 LWT \u2192 Dashboard reflects OFFLINE."},
]

_active_runners: dict[str, ScenarioRunner] = {}
_run_history: dict[str, RunReport] = {}
_ws_clients: list[WebSocket] = []


class RunRequest(BaseModel):
    scenario_id: str
    params: dict[str, Any] = {}


class SkipRequest(BaseModel):
    run_id: str
    stage_id: str


class AbortRequest(BaseModel):
    run_id: str


@app.on_event("startup")  # pyright: ignore[reportDeprecated]
async def startup() -> None:
    bus.start()


# REST API

@app.get("/api/scenarios")
async def list_scenarios() -> list[dict[str, str]]:
    return SCENARIO_META


@app.get("/api/broker/status")
async def broker_status() -> dict[str, bool]:
    return {"connected": bus.connected}


@app.get("/api/transcript")
async def transcript(limit: int = 200) -> list[dict[str, Any]]:
    return bus.get_transcript(limit)


@app.get("/api/runs")
async def list_runs() -> list[dict[str, Any]]:
    return [r.as_dict() for r in _run_history.values()]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    if run_id in _active_runners:
        return _active_runners[run_id].report.as_dict()
    if run_id in _run_history:
        return _run_history[run_id].as_dict()
    return {"error": "not found"}


@app.post("/api/runs/start")
async def start_run(req: RunRequest) -> dict[str, str]:
    builder = SCENARIO_BUILDERS.get(req.scenario_id)
    if not builder:
        return {"error": f"unknown scenario {req.scenario_id}"}

    scenario: Scenario = builder(**req.params) if req.params else builder()

    async def on_update(report: RunReport) -> None:
        _run_history[report.run_id] = report
        await _broadcast({"type": "run_update", "run": report.as_dict()})

    runner = ScenarioRunner(
        scenario=scenario,
        mqtt_subscribe=bus.subscribe,
        mqtt_unsubscribe=bus.unsubscribe,
        mqtt_publish=bus.publish,
        register_listener=bus.register_listener,
        on_update=on_update,
    )
    _active_runners[runner.report.run_id] = runner

    async def _drive() -> None:
        try:
            await runner.run()
        finally:
            _active_runners.pop(runner.report.run_id, None)

    asyncio.create_task(_drive())
    return {"run_id": runner.report.run_id, "scenario_id": scenario.scenario_id}


@app.post("/api/runs/skip")
async def skip_stage(req: SkipRequest) -> dict[str, bool | str]:
    runner = _active_runners.get(req.run_id)
    if not runner:
        return {"error": "run not active"}
    runner.request_skip(req.stage_id)
    return {"ok": True}


@app.post("/api/runs/abort")
async def abort_run(req: AbortRequest) -> dict[str, bool | str]:
    runner = _active_runners.get(req.run_id)
    if not runner:
        return {"error": "run not active"}
    runner.request_abort()
    return {"ok": True}


# WebSocket (live stream for the UI)

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "broker_status", "connected": bus.connected,
        }))
        while True:
            await asyncio.sleep(2)
            await websocket.send_text(json.dumps({
                "type": "broker_status", "connected": bus.connected,
            }))
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


async def _broadcast(message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    data = json.dumps(message)
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


_last_transcript_count: int = 0


async def _transcript_pump() -> None:
    global _last_transcript_count
    while True:
        await asyncio.sleep(0.5)
        msgs = bus.get_transcript(5000)
        if len(msgs) > _last_transcript_count:
            new_msgs = msgs[_last_transcript_count:]
            _last_transcript_count = len(msgs)
            for m in new_msgs:
                await _broadcast({"type": "mqtt_message", "message": m})


@app.on_event("startup")  # pyright: ignore[reportDeprecated]
async def start_transcript_pump() -> None:
    asyncio.create_task(_transcript_pump())


# Static UI

_static_dir: Final[str] = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(_static_dir, "index.html"))


app.mount("/static", StaticFiles(directory=_static_dir), name="static")
