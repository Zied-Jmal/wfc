"""
core/node/firefighting_drone_node.py - Firefighting Drone Node

WGS-84 GPS, litres payload, Wh battery, wind-corrected drops.
All telemetry fields carry real ISO-unit values:

    position             : (lat_deg, lon_deg)  WGS-84 decimal degrees
    altitude_m_amsl      : metres above mean sea level
    battery_wh           : watt-hours remaining
    battery_pct          : fraction 0.0-1.0
    payload_litres       : litres of suppressant remaining (L)
    payload_kg           : mass of remaining suppressant (kg)
    litres_delivered     : total litres dropped on fire (L)
    suppression_pct      : effectiveness 0.0-1.0 (litres vs. fire area)
    drop_passes          : integer count of drop passes completed
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.node.field_node import FieldNode
from core.utils.logger import log

# Physical engine (action layer V2)
from action.gps import GPSCoord
from action.wind import WindModel
from action.suppression import SuppressionActionEngine, SuppressionState
from action.resources import (
    DroneResourceModel, BATTERY_CAPACITY_WH, TANK_CAPACITY_L,
)

from wfc_shared.enums.capabilities import FIREFIGHTING, RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY
from wfc_shared.enums.command_types import DISPATCH_DRONE, RECALL_DRONE, UPDATE_TASK
from wfc_shared.enums.node_types import FIREFIGHTING_DRONE
from wfc_shared.enums.topics import telemetry_topic
from wfc_shared.schemas.telemetry import DroneTelemetry


class FirefightingDroneNode(FieldNode):
    """
    Suppression drone. Delivers water/retardant using wind-corrected approach.

    Physical layer: SuppressionActionEngine (approach/drop/egress, WGS-84).
    Resource layer: DroneResourceModel (Wh battery, litres payload, RSSI).
    Wind layer    : WindModel (approach vector computed from real wind direction).

    Args:
        node_id: Unique drone identifier.
        zone: Operational zone label.
        home_gps: Home GPS coordinate.
        leader_id: ID of the swarm leader this drone reports to.
        wind: Shared wind model (created with defaults if None).
        initial_battery_wh: Starting battery energy in watt-hours.
        initial_payload_l: Starting suppressant volume in litres.
        payload_type: Suppressant type ("water" or "retardant").
    """

    def __init__(
        self,
        node_id:         str,
        zone:            str,
        home_gps:        GPSCoord,
        leader_id:       str,
        wind:            WindModel | None = None,
        initial_battery_wh:  float = BATTERY_CAPACITY_WH,
        initial_payload_l:   float = TANK_CAPACITY_L,
        payload_type:        str   = "water",   # "water" | "retardant"
    ) -> None:
        super().__init__(
            node_id=node_id,
            node_type=FIREFIGHTING_DRONE,
            capabilities=[RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY, FIREFIGHTING],
            zone=zone,
            location=[home_gps.lat_deg, home_gps.lon_deg],
        )
        self._leader_id   = leader_id
        self._home_gps    = home_gps
        self._task        = "IDLE"
        self._running     = False
        self._telem_thread: threading.Thread | None = None

        # Wind (shared with scouts in same zone)
        self._wind = wind or WindModel(mean_speed_mps=5.0, mean_dir_deg=225.0)

        # Resource model
        self._resources = DroneResourceModel(
            initial_battery_wh=initial_battery_wh,
            initial_payload_l=initial_payload_l,
            payload_type=payload_type,
            base_station_dist_m=0.0,
        )

        # Physical engine
        self._action = SuppressionActionEngine(
            drone_id=node_id,
            home=home_gps,
            wind=self._wind,
            resources=self._resources,
            payload_type=payload_type,
        )

    # LIFECYCLE

    def start(self) -> None:
        """
        Start the firefighting drone - begin telemetry loop.

        Args:
            None

        Returns:
            None
        """
        super().start()
        self._running = True
        self._telem_thread = threading.Thread(
            target=self._telemetry_loop, daemon=True, name=f"telem-{self.node_id}"
        )
        self._telem_thread.start()
        log("FirefightingDroneNode",
            f"{self.node_id} started - home=({self._home_gps.lat_deg:.5f},"
            f"{self._home_gps.lon_deg:.5f}) payload={self._resources.payload_litres:.1f} L"
            f" leader={self._leader_id}",
            channel="SYSTEM")

    def stop(self) -> None:
        """Stop the firefighting drone and shut down movement engine."""
        self._running = False
        self._action._movement.shutdown()  # pyright: ignore[reportPrivateUsage]
        super().stop()

    # RE-PARENTING (spec Part 9)

    def _on_registry_announce(self, payload: dict[str, Any]) -> None:
        node_id = payload.get("node_id", "")
        caps    = payload.get("capabilities", [])
        if "SWARM_LEAD" in caps and node_id != self._leader_id:
            old = self._leader_id
            self._leader_id = node_id
            log("FirefightingDroneNode", f"re-parented: {old} → {node_id}", channel="SYSTEM")

    # COMMAND EXECUTION (from leader)

    def _execute_command(self, command_type: str, fire_payload: dict[str, Any], trace_id: str) -> None:
        """Execute a command from the swarm leader."""
        if command_type == DISPATCH_DRONE:
            target = fire_payload.get("target_pos")  # [lat, lon]
            severity = fire_payload.get("severity", "MEDIUM")
            area_m2 = fire_payload.get("fire_area_m2")  # optional, from scout report
            if target and len(target) >= 2:
                fire_gps = GPSCoord(target[0], target[1], self._home_gps.alt_m)
                self._task = "SUPPRESSING"
                self._action.dispatch_to(fire_gps, severity=severity, fire_area_m2=area_m2)
                log("FirefightingDroneNode",
                    f"DISPATCH SUPPRESSING fire=({target[0]:.5f},{target[1]:.5f})"
                    f" sev={severity} payload={self._resources.payload_litres:.1f} L",
                    channel="COMMANDS")
        elif command_type == RECALL_DRONE:
            self._task = "RETURNING"
            self._action.recall()
            log("FirefightingDroneNode", "RECALL RETURNING", channel="COMMANDS")
        elif command_type == UPDATE_TASK:
            new_task = fire_payload.get("task", self._task)
            target = fire_payload.get("target_pos")
            severity = fire_payload.get("severity", "MEDIUM")
            self._task = new_task
            if target and new_task == "SUPPRESSING" and len(target) >= 2:
                fire_gps = GPSCoord(target[0], target[1], self._home_gps.alt_m)
                self._action.dispatch_to(fire_gps, severity=severity)
            elif new_task == "RETURNING":
                self._action.recall()
            log("FirefightingDroneNode", f"UPDATE_TASK {new_task}", channel="COMMANDS")
        else:
            log("FirefightingDroneNode",
                f"unknown command: {command_type}", channel="COMMANDS")

    # TELEMETRY LOOP (every 2s)

    def _telemetry_loop(self) -> None:
        """Background telemetry publish loop (every 2s)."""
        DT = 2.0   # s
        while self._running:
            time.sleep(DT)
            try:
                # Tick physical engine (movement + resource drain internally)
                self._action.tick(dt=DT)

                # Sync task from engine state
                state_map = {
                    SuppressionState.IDLE:       "IDLE",
                    SuppressionState.TRANSITING: "SUPPRESSING",
                    SuppressionState.APPROACH:   "SUPPRESSING",
                    SuppressionState.DROPPING:   "SUPPRESSING",
                    SuppressionState.EGRESS:     "RETURNING",
                    SuppressionState.RETURNING:  "RETURNING",
                }
                self._task = state_map.get(self._action.state, self._task)

                # Emergency RTB on low battery
                if self._resources.should_return_to_base and self._task not in ("RETURNING", "IDLE"):
                    self._task = "RETURNING"
                    self._action.recall()
                    log("FirefightingDroneNode",
                        f"{self.node_id} LOW BATTERY ({self._resources.battery_wh:.1f} Wh) → RTB",
                        channel="SYSTEM")

                telem = self._build_telemetry()
                self.mqtt.publish(
                    telemetry_topic(self.node_id),
                    telem.model_dump(),
                    qos=0,
                )
            except Exception as exc:
                log("FirefightingDroneNode", f"telemetry error: {exc}", channel="SYSTEM")

    # TELEMETRY CONSTRUCTION

    def _build_telemetry(self) -> DroneTelemetry:
        """Build a DroneTelemetry payload from current position and resource state."""
        pos = self._action.position_gps   # WGS-84, with GPS noise

        dist_to_gcs = _haversine_approx_m(
            self._home_gps.lat_deg, self._home_gps.lon_deg,
            pos.lat_deg, pos.lon_deg,
        )
        conn = self._resources.connectivity(dist_to_gcs)

        return DroneTelemetry(
            drone_id=self.node_id,
            leader_id=self._leader_id,
            timestamp=time.time(),

            # ISO position
            position=(pos.lat_deg, pos.lon_deg),             # WGS-84 decimal degrees
            altitude_m_amsl=self._action.altitude_m_amsl,   # m AMSL

            # ISO resource values
            battery_wh=self._resources.battery_wh,          # Wh
            battery_pct=self._resources.battery_pct,        # 0.0-1.0
            payload_litres=self._resources.payload_litres,  # L
            payload_kg=self._resources.payload_kg,          # kg
            task=self._task,  # pyright: ignore[reportArgumentType]
            connectivity=conn,  # pyright: ignore[reportArgumentType]

            # Suppression metrics
            litres_delivered=self._action.litres_delivered,             # L total
            suppression_effectiveness_pct=self._action.suppression_effectiveness_pct,  # 0.0-1.0
            drop_passes=self._action.drop_passes,                       # int
            pump_active=self._action.pump_active,                       # bool

            # Firefighters carry no scouting sensors
        )


def _haversine_approx_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    R  = 6_371_000.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = p2 - p1; dl = math.radians(lon2 - lon1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))
