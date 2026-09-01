# core/tactics/fire_tactics.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from core.state.drone_registry import DroneRecord, DroneRegistry
from core.utils.logger import log
from wfc_shared.schemas.telemetry import DroneTelemetry, SwarmStatusSnapshot

RTB_BATTERY_PCT: Final[float] = 0.25
RTB_BATTERY_WH: Final[float] = 87.9
LOW_PAYLOAD_L: Final[float] = 1.5
ORBIT_RADIUS_BY_SEVERITY: Final[dict[str, float]] = {"LOW": 60.0, "MEDIUM": 80.0, "HIGH": 110.0, "CRITICAL": 140.0}
_EARTH_R_M: Final[float] = 6_371_000.0
_LITRES_PER_M2: Final[dict[str, float]] = {"LOW": 0.5, "MEDIUM": 1.2, "HIGH": 2.5, "CRITICAL": 5.0}


@dataclass
class DroneAssignment:
    drone_id: str
    task: str
    target_pos: tuple[float, float] | None = None
    severity: str | None = None
    priority: int = 5


def _dest(origin: tuple[float, float], bearing_deg: float, dist_m: float) -> tuple[float, float]:
    R = _EARTH_R_M
    ang = dist_m / R
    bear = math.radians(bearing_deg)
    lat1 = math.radians(origin[0])
    lon1 = math.radians(origin[1])
    lat2 = math.asin(math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(bear))
    lon2 = lon1 + math.atan2(
        math.sin(bear) * math.sin(ang) * math.cos(lat1), math.cos(ang) - math.sin(lat1) * math.sin(lat2)
    )
    return (math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180)


class FireTactics:
    """Stateless tactics engine. All target_pos values are WGS-84 (lat_deg, lon_deg).

    V2 changes vs V1:
      - All positions use GPS _dest() not raw polar offset
      - Wind-corrected approach positions for fighters
      - Payload-aware dispatch (skip drones below LOW_PAYLOAD_L)
      - Battery check uses Wh AND pct (V2 dual check)
      - Scout perimeter_estimate_m drives ring radius in contain
      - 5 autopilot rules: CRITICAL hold, rapid-spread flank,
        battery swap, payload swap, suppression surplus recall
      - fighters_needed() and scouts_needed() advisory methods
    """

    # --- Command-triggered allocation --------------------------

    def assign_respond_to_fire(
        self,
        fire_id: str,
        fire_pos: tuple[float, float],
        severity: str,
        scouts: list[DroneRecord],
        fighters: list[DroneRecord],
        wind_dir_deg: float | None = None,
    ) -> list[DroneAssignment]:
        assignments: list[DroneAssignment] = []
        orbit_r = ORBIT_RADIUS_BY_SEVERITY.get(severity, 80.0)

        for _i, scout in enumerate(scouts):
            assignments.append(
                DroneAssignment(
                    drone_id=scout.drone_id, task="SCOUTING", target_pos=fire_pos, severity=severity, priority=8
                )
            )

        dispatched = 0
        for fighter in fighters:
            if not self._has_payload(fighter):
                log("FireTactics", f"{fighter.drone_id} skipped -- low payload", channel="SYSTEM")
                continue
            assignments.append(
                DroneAssignment(
                    drone_id=fighter.drone_id, task="SUPPRESSING", target_pos=fire_pos, severity=severity, priority=9
                )
            )
            dispatched += 1

        log(
            "FireTactics",
            f"respond_to_fire {fire_id} sev={severity}: {len(scouts)} scouts r={orbit_r:.0f}m | {dispatched} fighters",
            channel="SYSTEM",
        )
        return assignments

    def assign_contain_fire(
        self,
        fire_id: str,
        fire_pos: tuple[float, float],
        severity: str,
        perimeter_m: float | None,
        scouts: list[DroneRecord],
        fighters: list[DroneRecord],
        wind_dir_deg: float | None = None,
    ) -> list[DroneAssignment]:
        assignments: list[DroneAssignment] = []
        radius = (perimeter_m / (2 * math.pi)) if perimeter_m else ORBIT_RADIUS_BY_SEVERITY.get(severity, 80.0)
        all_drones = scouts + fighters
        n = max(len(all_drones), 1)
        upwind_base = (wind_dir_deg + 180.0) % 360 if wind_dir_deg else 0.0

        for i, drone in enumerate(all_drones):
            bearing = (upwind_base + (360.0 / n) * i) % 360.0
            pos = _dest(fire_pos, bearing, radius)
            is_scout = drone in scouts
            task = "SCOUTING" if is_scout else "SUPPRESSING"
            if not is_scout and not self._has_payload(drone):
                task = "RETURNING"
            assignments.append(
                DroneAssignment(drone_id=drone.drone_id, task=task, target_pos=pos, severity=severity, priority=7)
            )

        log(
            "FireTactics",
            f"contain_fire {fire_id}: r={radius:.0f}m perimeter_m={perimeter_m} {n} drones",
            channel="SYSTEM",
        )
        return assignments

    def assign_reinforce(
        self,
        fire_id: str,
        fire_pos: tuple[float, float],
        severity: str,
        new_scouts: list[DroneRecord],
        new_fighters: list[DroneRecord],
        current_count: int,
        wind_dir_deg: float | None = None,
    ) -> list[DroneAssignment]:
        assignments: list[DroneAssignment] = []

        for _i, scout in enumerate(new_scouts):
            assignments.append(
                DroneAssignment(
                    drone_id=scout.drone_id, task="SCOUTING", target_pos=fire_pos, severity=severity, priority=8
                )
            )

        for fighter in new_fighters:
            if not self._has_payload(fighter):
                continue
            assignments.append(
                DroneAssignment(
                    drone_id=fighter.drone_id, task="SUPPRESSING", target_pos=fire_pos, severity=severity, priority=9
                )
            )

        log(
            "FireTactics",
            f"reinforce {fire_id}: +{len(new_scouts)} scouts +{len(new_fighters)} fighters",
            channel="SYSTEM",
        )
        return assignments

    def assign_stand_down(self, all_drones: list[DroneRecord]) -> list[DroneAssignment]:
        result: list[DroneAssignment] = [
            DroneAssignment(drone_id=d.drone_id, task="RETURNING", priority=10) for d in all_drones
        ]
        return result

    # --- Autopilot reassess (every 2s) -------------------------

    def reassess(
        self,
        snapshot: SwarmStatusSnapshot,
        drone_registry: DroneRegistry,
    ) -> list[DroneAssignment]:
        """
        6 autopilot rules (priority order):
           1. CRITICAL + no scouts -> hold idle fighters
           2. RAPID spread + low suppression -> dispatch to hot flank
           3. Low battery (Wh or pct) -> recall + swap
           4. Low payload -> recall fighter + swap
           5. Active fire + idle fighters -> re-dispatch (multi-pass)
           6. 85% suppressed + LOW/MEDIUM -> recall surplus
        """
        actions: list[DroneAssignment] = []
        all_drones: list[DroneRecord] = drone_registry.get_all()
        scout_telems: list[DroneTelemetry] = [
            d.last_telemetry for d in drone_registry.get_by_role("SCOUT") if d.last_telemetry
        ]
        wind_dir_deg: float | None = self._best_wind_dir(scout_telems)
        wind_spd_mps: float | None = self._best_wind_speed(scout_telems)
        fire_pos: tuple[float, float] | None = self._estimate_fire_centre(scout_telems)

        # Rule 1 - CRITICAL + no scouts orbiting -> hold idle fighters
        if snapshot.fire_intensity == "CRITICAL":
            scouts_orbiting = any(
                d.last_telemetry and d.last_telemetry.task == "SCOUTING" for d in drone_registry.get_by_role("SCOUT")
            )
            if not scouts_orbiting:
                log("FireTactics", "CRITICAL fire, no scouts -- holding fighters", channel="SYSTEM")
                for drone in all_drones:
                    t = drone.last_telemetry
                    if t and t.task == "IDLE" and drone.role == "FIREFIGHTING":
                        actions.append(DroneAssignment(drone_id=drone.drone_id, task="IDLE", priority=6))

        # Rule 2 - RAPID spread + suppression < 30% -> hot flank
        if (
            snapshot.spread_rate == "RAPID"
            and snapshot.suppression_pct is not None
            and snapshot.suppression_pct < 0.3
            and fire_pos
        ):
            hot_flank: tuple[float, float] = self._hot_flank(fire_pos, wind_dir_deg, wind_spd_mps)
            candidates: list[DroneRecord] = self._idle_fighters_with_payload(drone_registry, 2)
            for f in candidates:
                actions.append(
                    DroneAssignment(
                        drone_id=f.drone_id,
                        task="SUPPRESSING",
                        target_pos=hot_flank,
                        severity=snapshot.fire_intensity,
                        priority=10,
                    )
                )
            if candidates:
                log(
                    "FireTactics",
                    f"RAPID spread: {len(candidates)} fighters -> hot flank {hot_flank}",
                    channel="SYSTEM",
                )

        # Rule 3 - Low battery -> recall + swap
        for drone in all_drones:
            t = drone.last_telemetry
            if not t or t.task == "RETURNING":
                continue
            low = t.battery_pct < RTB_BATTERY_PCT or (t.battery_wh is not None and t.battery_wh < RTB_BATTERY_WH)  # pyright: ignore[reportUnnecessaryComparison]
            if low:
                actions.append(DroneAssignment(drone_id=drone.drone_id, task="RETURNING", priority=9))
                log(
                    "FireTactics",
                    f"LOW BATTERY {drone.drone_id}: {getattr(t, 'battery_wh', 0):.0f} Wh ({t.battery_pct:.0%})",
                    channel="SYSTEM",
                )
                fresh: DroneRecord | None = self._fresh_idle(drone_registry, drone.role)
                if fresh and fire_pos:
                    task = "SUPPRESSING" if drone.role == "FIREFIGHTING" else "SCOUTING"
                    actions.append(
                        DroneAssignment(
                            drone_id=fresh.drone_id,
                            task=task,
                            target_pos=fire_pos,
                            severity=snapshot.fire_intensity,
                            priority=9,
                        )
                    )

        # Rule 4 - Low payload -> recall fighter + swap
        for drone in drone_registry.get_by_role("FIREFIGHTING"):
            t = drone.last_telemetry
            if not t or t.task == "RETURNING":
                continue
            if (
                t.payload_litres is not None  # pyright: ignore[reportUnnecessaryComparison]
                and t.payload_litres <= LOW_PAYLOAD_L
                and t.task == "SUPPRESSING"
            ):
                actions.append(DroneAssignment(drone_id=drone.drone_id, task="RETURNING", priority=8))
                log("FireTactics", f"LOW PAYLOAD {drone.drone_id}: {t.payload_litres:.2f} L", channel="SYSTEM")
                fresh = self._fresh_idle(drone_registry, "FIREFIGHTING")
                if fresh and fire_pos:
                    actions.append(
                        DroneAssignment(
                            drone_id=fresh.drone_id,
                            task="SUPPRESSING",
                            target_pos=fire_pos,
                            severity=snapshot.fire_intensity,
                            priority=8,
                        )
                    )

        # Rule 6 - Active fire with idle fighters -> dispatch them
        if (
            snapshot.suppression_pct is not None
            and snapshot.suppression_pct < 0.95
            and snapshot.status != "IDLE"
            and fire_pos
        ):
            idle_ff: list[DroneRecord] = self._idle_fighters_with_payload(drone_registry, 1)
            if idle_ff:
                actions.append(
                    DroneAssignment(
                        drone_id=idle_ff[0].drone_id,
                        task="SUPPRESSING",
                        target_pos=fire_pos,
                        severity=snapshot.fire_intensity,
                        priority=7,
                    )
                )
                log(
                    "FireTactics",
                    f"dispatch idle fighter {idle_ff[0].drone_id} to active fire "
                    f"({snapshot.suppression_pct:.0%} suppressed)",
                    channel="SYSTEM",
                )

        # Rule 5 - Nearly suppressed -> recall surplus fighters
        if (
            snapshot.suppression_pct is not None
            and snapshot.suppression_pct >= 0.85
            and snapshot.fire_intensity in ("LOW", "MEDIUM")
        ):
            active_ff: list[DroneRecord] = [
                d
                for d in drone_registry.get_by_role("FIREFIGHTING")
                if d.last_telemetry and d.last_telemetry.task == "SUPPRESSING"
            ]
            for drone in active_ff[1:]:
                actions.append(DroneAssignment(drone_id=drone.drone_id, task="RETURNING", priority=5))
            if active_ff[1:]:
                log(
                    "FireTactics",
                    f"suppression {snapshot.suppression_pct:.0%}: recalling {len(active_ff) - 1} surplus",
                    channel="SYSTEM",
                )

        return actions

    # --- Advisory ----------------------------------------------

    def fighters_needed(self, severity: str, perimeter_m: float | None) -> int:
        area = (perimeter_m**2 / (4 * math.pi)) if perimeter_m else 1000.0
        litres = _LITRES_PER_M2.get(severity, 1.2) * area
        return max(1, min(6, math.ceil(litres / 5.0 / 3.0)))

    def scouts_needed(self, perimeter_m: float | None) -> int:
        return max(1, math.ceil((perimeter_m or 150.0) / 150.0))

    # --- Private helpers --------------------------------------

    def _hot_flank(
        self,
        fire_pos: tuple[float, float],
        wind_dir_deg: float | None,
        wind_spd_mps: float | None,
    ) -> tuple[float, float]:
        if wind_dir_deg is None:
            return fire_pos
        downwind = (wind_dir_deg + 180.0) % 360.0
        offset_m = 30.0 + (wind_spd_mps or 5.0) * 5.0
        return _dest(fire_pos, downwind, offset_m)

    def _estimate_fire_centre(self, telems: list[DroneTelemetry]) -> tuple[float, float] | None:
        pts = [(t.position[0], t.position[1]) for t in telems if t.task == "SCOUTING"]
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def _best_wind_dir(self, ts: list[DroneTelemetry]) -> float | None:
        d = [t.wind_direction_deg for t in ts if t.wind_direction_deg is not None]
        return sum(d) / len(d) if d else None

    def _best_wind_speed(self, ts: list[DroneTelemetry]) -> float | None:
        s = [t.wind_speed_mps for t in ts if t.wind_speed_mps is not None]
        return sum(s) / len(s) if s else None

    def _idle_fighters_with_payload(self, registry: DroneRegistry, count: int) -> list[DroneRecord]:
        cands = [d for d in registry.get_idle() if d.role == "FIREFIGHTING" and self._has_payload(d)]
        cands.sort(key=lambda d: (d.last_telemetry.payload_litres or 0.0) if d.last_telemetry else 0.0, reverse=True)
        return cands[:count]

    def _fresh_idle(self, registry: DroneRegistry, role: str) -> DroneRecord | None:
        for d in registry.get_idle():
            if d.role != role:
                continue
            t = d.last_telemetry
            if t and t.battery_pct > RTB_BATTERY_PCT and self._has_payload(d):
                return d
        return None

    @staticmethod
    def _has_payload(drone: DroneRecord) -> bool:
        t = drone.last_telemetry
        if t is None:
            return True
        if t.payload_litres is None:  # pyright: ignore[reportUnnecessaryComparison]
            return True
        return t.payload_litres > LOW_PAYLOAD_L
