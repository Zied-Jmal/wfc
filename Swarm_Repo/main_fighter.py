#!/usr/bin/env python3
# main_fighter.py - Entrypoint for FirefightingDroneNode
# ENV VARS (all have safe defaults):
#   NODE_ID              : drone node id (e.g. fd-A-01)
#   NODE_ZONE            : operational zone (e.g. zone_alpha)
#   NODE_LOCATION        : "lat_deg,lon_deg"  WGS-84  (e.g. "36.8065,10.1815")
#   HOME_ALT_M           : home altitude m AMSL (default 50.0)
#   LEADER_ID            : parent leader node_id (default sl-A-01)
#   PAYLOAD_TYPE         : water | retardant (default water)
#   INITIAL_PAYLOAD_L    : starting payload litres (default 10.0 = full tank)
#   INITIAL_BATTERY_WH   : starting battery Wh (default 585.9 = DJI TB60 full)
#   WIND_SPEED_MPS       : initial mean wind speed (default 5.0)
#   WIND_DIR_DEG         : initial wind FROM direction °T (default 225.0)
#   TURBULENCE           : NONE|LIGHT|MODERATE|SEVERE (default LIGHT)
#   MQTT_HOST            : broker hostname (default localhost)
#   MQTT_PORT            : broker port    (default 1883)
from __future__ import annotations

import os
import signal
import sys
from types import FrameType

from action.gps import GPSCoord
from action.wind import WindModel

from core.node.firefighting_drone_node import FirefightingDroneNode
from core.utils.config import (  # pyright: ignore[reportUnknownVariableType]
    get_initial_battery_wh,
    get_initial_payload_l,
    get_leader_id,
    get_node_gps,
    get_node_id,
    get_node_zone,
    get_payload_type,
    get_turbulence,
    get_wind_dir_deg,
    get_wind_speed_mps,
)


def main() -> None:
    """Entrypoint for FirefightingDroneNode - starts a firefighting drone node."""

    node_id = get_node_id()
    zone = get_node_zone() or "zone_alpha"
    home_gps: GPSCoord = get_node_gps()  # pyright: ignore[reportUnknownVariableType]
    leader = get_leader_id()
    payload_type = get_payload_type()
    payload_l = get_initial_payload_l()
    battery_wh = get_initial_battery_wh()

    wind = WindModel(
        mean_speed_mps=get_wind_speed_mps(),
        mean_dir_deg=get_wind_dir_deg(),
        turbulence=get_turbulence(),
        altitude_ref_m=10.0,
    )

    node = FirefightingDroneNode(
        node_id=node_id,
        zone=zone,
        home_gps=home_gps,  # pyright: ignore[reportArgumentType, reportUnknownArgumentType]
        leader_id=leader,
        wind=wind,
        initial_battery_wh=battery_wh,
        initial_payload_l=payload_l,
        payload_type=payload_type,
    )

    def _shutdown(sig: int | None, frame: FrameType | None) -> None:
        print(f"\n[{node_id}] shutting down…", flush=True)
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    node.start()

    print(
        f"[{node_id}] FIREFIGHTER started\n"
        f"  home     : {home_gps}\n"
        f"  zone     : {zone}\n"
        f"  leader   : {leader}\n"
        f"  payload  : {payload_l:.1f} L {payload_type}\n"
        f"  battery  : {battery_wh:.1f} Wh\n"
        f"  wind     : {get_wind_speed_mps()} m/s FROM {get_wind_dir_deg()}°T"
        f" [{get_turbulence()} turbulence]\n"
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
