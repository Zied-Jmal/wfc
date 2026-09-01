#!/usr/bin/env python3
# main_scout.py - Entrypoint for ScoutDroneNode
# ENV VARS (all have safe defaults):
#   NODE_ID              : drone node id (e.g. sd-A-01)
#   NODE_ZONE            : operational zone (e.g. zone_alpha)
#   NODE_LOCATION        : "lat_deg,lon_deg"  WGS-84  (e.g. "36.8065,10.1815")
#   HOME_ALT_M           : home altitude m AMSL (default 50.0)
#   LEADER_ID            : parent leader node_id (default sl-A-01)
#   WIND_SPEED_MPS       : initial mean wind speed (default 5.0)
#   WIND_DIR_DEG         : initial wind FROM direction °T (default 225.0)
#   TURBULENCE           : NONE|LIGHT|MODERATE|SEVERE (default LIGHT)
#   INITIAL_BATTERY_WH   : starting battery Wh (default 585.9 = DJI TB60 full)
#   MQTT_HOST            : broker hostname (default localhost)
#   MQTT_PORT            : broker port    (default 1883)
from __future__ import annotations

import os
import signal
import sys
from types import FrameType

from action.gps import GPSCoord
from action.wind import WindModel

from core.node.scout_drone_node import ScoutDroneNode
from core.utils.config import (  # pyright: ignore[reportUnknownVariableType]
    get_initial_battery_wh,
    get_leader_id,
    get_node_gps,
    get_node_id,
    get_node_zone,
    get_turbulence,
    get_wind_dir_deg,
    get_wind_speed_mps,
)


def main() -> None:
    """Entrypoint for ScoutDroneNode - starts a scout drone node."""

    node_id = get_node_id()
    zone = get_node_zone() or "zone_alpha"
    home_gps: GPSCoord = get_node_gps()  # pyright: ignore[reportUnknownVariableType]
    leader = get_leader_id()

    # Wind model
    # Each drone gets its own WindModel instance.
    # In a zone with shared physics, pass the same WindModel to all
    # drones in the zone (requires a coordinator process - future work).
    wind = WindModel(
        mean_speed_mps=get_wind_speed_mps(),
        mean_dir_deg=get_wind_dir_deg(),
        turbulence=get_turbulence(),
        altitude_ref_m=10.0,
    )

    node = ScoutDroneNode(
        node_id=node_id,
        zone=zone,
        home_gps=home_gps,  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]
        leader_id=leader,
        wind=wind,
        initial_battery_wh=get_initial_battery_wh(),
    )

    def _shutdown(sig: int | None, frame: FrameType | None) -> None:
        print(f"\n[{node_id}] shutting down…", flush=True)
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    node.start()

    print(
        f"[{node_id}] SCOUT started\n"
        f"  home     : {home_gps}\n"
        f"  zone     : {zone}\n"
        f"  leader   : {leader}\n"
        f"  wind     : {get_wind_speed_mps()} m/s FROM {get_wind_dir_deg()}°T"
        f" [{get_turbulence()} turbulence]\n"
        f"  battery  : {get_initial_battery_wh():.1f} Wh\n"
        f"  broker   : {os.getenv('MQTT_HOST', 'localhost')}:{os.getenv('MQTT_PORT', '1883')}",
        flush=True,
    )

    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(None, None)


if __name__ == "__main__":
    main()
