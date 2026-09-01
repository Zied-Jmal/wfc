#!/usr/bin/env python3
# main.py - Start dashboard (8080) + map (8081) in one process.
from __future__ import annotations

import asyncio
import os
from typing import Final

import uvicorn

DASHBOARD_PORT: Final[int] = int(os.getenv("DASHBOARD_PORT", "8080"))
MAP_PORT: Final[int] = int(os.getenv("MAP_PORT", "8081"))


async def main() -> None:
    from dashboard.map_server import map_app
    from dashboard.server import app

    cfg_dash = uvicorn.Config(app, host="0.0.0.0", port=DASHBOARD_PORT, log_level="info")
    cfg_map = uvicorn.Config(map_app, host="0.0.0.0", port=MAP_PORT, log_level="info")

    await asyncio.gather(
        uvicorn.Server(cfg_dash).serve(),
        uvicorn.Server(cfg_map).serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
