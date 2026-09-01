"""
core/node/scout_drone_node.py - Scout Drone Node

WGS-84 GPS, physics sensors, Wh battery, ISO units throughout.
All telemetry fields carry real ISO-unit values:
  position         : (lat_deg, lon_deg)  WGS-84 decimal degrees
  altitude_m_amsl  : metres above mean sea level
  battery_wh       : watt-hours remaining
  battery_pct      : fraction 0.0-1.0
  thermal_peak_temp_c        : °C  (Stefan-Boltzmann + inverse-square model)
  thermal_coverage_pct       : fraction 0.0-1.0
  smoke_density_mg_m3        : mg/m³  (Gaussian plume dispersion)
  flame_height_m             : m  (Heskestad correlation)
  wind_speed_mps             : m/s
  wind_direction_deg         : °T meteorological (FROM direction)
  distance_to_flame_m        : m  (laser rangefinder slant range)
  perimeter_estimate_m       : m  (updated after each orbit pass)
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Physical engine (action layer V2)
from action.gps import GPSCoord
from action.resources import BATTERY_CAPACITY_WH, DroneResourceModel
from action.scouting import ScoutActionEngine, ScoutState
from action.wind import WindModel

from core.node.field_node import FieldNode
from core.utils.logger import log
from wfc_shared.enums.capabilities import HEARTBEAT, RECEIVE_COMMANDS, SCOUT, TELEMETRY
from wfc_shared.enums.command_types import DISPATCH_DRONE, RECALL_DRONE, UPDATE_TASK
from wfc_shared.enums.node_types import SCOUT_DRONE
from wfc_shared.enums.topics import telemetry_topic
from wfc_shared.schemas.telemetry import DroneTelemetry


class ScoutDroneNode(FieldNode):
    """
    Reconnaissance drone. Sends physics-based telemetry to leader every 2s.

    Physical layer: ScoutActionEngine (orbit, grid, sensors, GPS).
    Resource layer: DroneResourceModel (Wh battery, RSSI connectivity).
    Wind layer    : WindModel (Dryden turbulence, fire plume).

    Args:
        node_id: Unique drone identifier.
        zone: Operational zone label.
        home_gps: Home GPS coordinate.
        leader_id: ID of the swarm leader this drone reports to.
        wind: Shared wind model (created with defaults if None).
        initial_battery_wh: Starting battery energy in watt-hours.
    """

    def __init__(
        self,
        node_id: str,
        zone: str,
        home_gps: GPSCoord,
        leader_id: str,
        wind: WindModel | None = None,
        initial_battery_wh: float = BATTERY_CAPACITY_WH,
    ) -> None:
        super().__init__(
            node_id=node_id,
            node_type=SCOUT_DRONE,
            capabilities=[RECEIVE_COMMANDS, HEARTBEAT, TELEMETRY, SCOUT],
            zone=zone,
            location=[home_gps.lat_deg, home_gps.lon_deg],
        )
        self._leader_id = leader_id
        self._home_gps = home_gps
        self._task = "IDLE"
        self._running = False
        self._telem_thread: threading.Thread | None = None

        # Wind model (shared with fire suppression drones in zone)
        self._wind = wind or WindModel(mean_speed_mps=5.0, mean_dir_deg=225.0)

        # Resource model
        self._resources = DroneResourceModel(
            initial_battery_wh=initial_battery_wh,
            initial_payload_l=0.0,  # scouts carry no liquid payload
            payload_type="water",
            base_station_dist_m=0.0,
        )

        # Physical engine
        self._action = ScoutActionEngine(
            drone_id=node_id,
            home=home_gps,
            wind=self._wind,
        )

    # LIFECYCLE

    def start(self) -> None:
        """
        Start the scout drone - begin telemetry loop.

        Args:
            None

        Returns:
            None
        """
        super().start()
        self._running = True
        self._telem_thread = threading.Thread(target=self._telemetry_loop, daemon=True, name=f"telem-{self.node_id}")
        self._telem_thread.start()
        log(
            "ScoutDroneNode",
            f"{self.node_id} started - home=({self._home_gps.lat_deg:.5f},"
            f"{self._home_gps.lon_deg:.5f}) leader={self._leader_id}",
            channel="SYSTEM",
        )

    def stop(self) -> None:
        """Stop the scout drone and shut down movement engine."""
        self._running = False
        self._action._movement.shutdown()  # pyright: ignore[reportPrivateUsage]
        super().stop()

    # RE-PARENTING (spec Part 9)

    def _on_registry_announce(self, payload: dict[str, Any]) -> None:
        node_id = payload.get("node_id", "")
        caps = payload.get("capabilities", [])
        if "SWARM_LEAD" in caps and node_id != self._leader_id:
            old = self._leader_id
            self._leader_id = node_id
            log("ScoutDroneNode", f"re-parented: {old} → {node_id}", channel="SYSTEM")

    # COMMAND EXECUTION (from leader)

    def _execute_command(self, command_type: str, fire_payload: dict[str, Any], trace_id: str) -> None:
        """Execute a command from the swarm leader."""
        if command_type == DISPATCH_DRONE:
            target = fire_payload.get("target_pos")  # [lat, lon] from leader
            severity = fire_payload.get("severity", "MEDIUM")
            if target and len(target) >= 2:
                fire_gps = GPSCoord(
                    lat_deg=target[0],
                    lon_deg=target[1],
                    alt_m=self._home_gps.alt_m,
                )
                self._task = "SCOUTING"
                self._action.dispatch_to(fire_gps, severity=severity)
                log(
                    "ScoutDroneNode",
                    f"DISPATCH SCOUTING fire=({target[0]:.5f},{target[1]:.5f}) sev={severity}",
                    channel="COMMANDS",
                )
        elif command_type == RECALL_DRONE:
            self._task = "RETURNING"
            self._action.recall()
            log("ScoutDroneNode", "RECALL RETURNING", channel="COMMANDS")
        elif command_type == UPDATE_TASK:
            new_task = fire_payload.get("task", self._task)
            target = fire_payload.get("target_pos")
            severity = fire_payload.get("severity", "MEDIUM")
            self._task = new_task
            if target and new_task == "SCOUTING" and len(target) >= 2:
                fire_gps = GPSCoord(target[0], target[1], self._home_gps.alt_m)
                self._action.dispatch_to(fire_gps, severity=severity)
            elif new_task == "RETURNING":
                self._action.recall()
            log("ScoutDroneNode", f"UPDATE_TASK {new_task}", channel="COMMANDS")
        else:
            log("ScoutDroneNode", f"unknown command: {command_type}", channel="COMMANDS")

    # TELEMETRY LOOP (every 2s)

    def _telemetry_loop(self) -> None:
        """Background telemetry publish loop (every 2s)."""
        DT = 2.0  # s
        while self._running:
            time.sleep(DT)
            try:
                is_moving = self._action.state in (ScoutState.TRANSITING, ScoutState.GRID_SWEEP)

                # Tick physical engine (movement + sensors)
                self._action.tick(dt=DT)

                # Tick resource model
                phase = "CRUISE" if is_moving else "HOVER"
                self._resources.tick(
                    dt=DT,
                    phase=phase,
                    pump_active=False,
                    sensors_on=(self._action.state != ScoutState.IDLE),
                )

                # Sync task label with engine state
                state_map = {
                    ScoutState.IDLE: "IDLE",
                    ScoutState.TRANSITING: "SCOUTING",
                    ScoutState.ORBITING: "SCOUTING",
                    ScoutState.GRID_SWEEP: "SCOUTING",
                    ScoutState.RETURNING: "RETURNING",
                }
                self._task = state_map.get(self._action.state, self._task)

                # Emergency RTB on low battery
                if self._resources.should_return_to_base and self._task != "RETURNING":
                    self._task = "RETURNING"
                    self._action.recall()
                    log(
                        "ScoutDroneNode",
                        f"{self.node_id} LOW BATTERY ({self._resources.battery_wh:.1f} Wh) → RTB",
                        channel="SYSTEM",
                    )

                telem = self._build_telemetry()
                self.mqtt.publish(
                    telemetry_topic(self.node_id),
                    telem.model_dump(),
                    qos=0,
                )
            except Exception as exc:
                log("ScoutDroneNode", f"telemetry error: {exc}", channel="SYSTEM")

    # TELEMETRY CONSTRUCTION

    def _build_telemetry(self) -> DroneTelemetry:
        """Build a DroneTelemetry payload from current sensor and resource state."""
        pos = self._action.position_gps  # WGS-84, noisy
        sr = self._action.last_sensor_readings  # physics-based readings

        # Connectivity from RSSI model
        dist_to_gcs = self._action.position_gps_true and _haversine_approx_m(
            self._home_gps.lat_deg,
            self._home_gps.lon_deg,
            pos.lat_deg,
            pos.lon_deg,
        )
        conn = self._resources.connectivity(dist_to_gcs)

        return DroneTelemetry(
            drone_id=self.node_id,
            leader_id=self._leader_id,
            timestamp=time.time(),  # UNIX epoch (s)
            # ISO position
            position=(pos.lat_deg, pos.lon_deg),  # WGS-84 decimal degrees
            altitude_m_amsl=self._action.altitude_m_amsl,  # m AMSL
            # ISO resource values
            battery_wh=self._resources.battery_wh,  # Wh remaining
            battery_pct=self._resources.battery_pct,  # 0.0-1.0
            payload_litres=0.0,  # L  (scouts carry no payload)
            payload_kg=0.0,  # kg
            task=self._task,  # pyright: ignore[reportArgumentType]
            connectivity=conn,  # pyright: ignore[reportArgumentType]
            # ISO sensor values (all physics-based)
            thermal_peak_temp_c=sr.get("thermal_peak_temp_c"),  # °C
            thermal_coverage_pct=sr.get("thermal_coverage_pct"),  # 0.0-1.0
            smoke_density_mg_m3=sr.get("smoke_density_mg_m3"),  # mg/m³
            smoke_optical_density=sr.get("smoke_optical_density"),  # 0.0-1.0
            flame_height_m=sr.get("flame_height_m"),  # m
            distance_to_flame_m=sr.get("distance_to_flame_m"),  # m
            wind_speed_mps=sr.get("wind_speed_mps"),  # m/s
            wind_direction_deg=sr.get("wind_direction_deg"),  # °T
            perimeter_estimate_m=sr.get("perimeter_estimate_m"),  # m
        )


def _haversine_approx_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate Haversine for short distances."""
    import math

    R = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
