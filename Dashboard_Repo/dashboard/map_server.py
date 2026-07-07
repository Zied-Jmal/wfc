# dashboard/map_server.py
# Separate FastAPI app for the live map (port 8081).
# Reads from the same SwarmState singleton - zero MQTT coupling.
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator, Final

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response

from dashboard.state import swarm_state

# Environment variables (same as main server)
MQTT_WS_HOST: Final[str] = os.getenv("MQTT_WS_HOST", "localhost")
MQTT_WS_PORT: Final[int] = int(os.getenv("MQTT_WS_PORT", "9001"))
MAP_PORT: Final[int] = int(os.getenv("MAP_PORT", "8081"))
DASHBOARD_PORT: Final[int] = int(os.getenv("DASHBOARD_PORT", "8080"))

map_app = FastAPI(title="WFC Live Map", version="2.0.0")
map_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Configuration endpoint (for the frontend)
@map_app.get("/config.js")
async def config_js() -> Response:
    """Serve configuration variables used by the map JavaScript."""

    js = (
        f'window.WFC_CONFIG = {{'
        f'"MQTT_WS_HOST":"{MQTT_WS_HOST}",'
        f'"MQTT_WS_PORT":{MQTT_WS_PORT},'
        f'"MAP_PORT":{MAP_PORT}'
        f'}};'
    )
    return Response(content=js, media_type="application/javascript")


# State endpoints
@map_app.get("/api/snapshot")
async def snapshot() -> dict[str, Any]:
    """Full snapshot of the current swarm state (V2 fields)."""
    return swarm_state.snapshot()


@map_app.get("/api/stream")
async def map_stream(request: Request) -> StreamingResponse:
    """Server‑sent events: pushes updated snapshots every 500 ms."""
    async def generator() -> AsyncGenerator[str, None]:
        while True:
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(swarm_state.snapshot())}\n\n"
            await asyncio.sleep(0.5)   # 2 Hz refresh for smooth map

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# Serve the map HTML
@map_app.get("/", response_class=HTMLResponse)
async def map_page() -> str:
    """Render the live map template."""
    import os
    template_path = os.path.join(os.path.dirname(__file__), "templates", "map.html")
    with open(template_path, encoding="utf-8") as f:
        return f.read()
