# core/aggregator/telemetry_aggregator.py
# Converts raw DroneTelemetry into SwarmStatusSnapshot.
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Final

from wfc_shared.schemas.telemetry import DroneTelemetry, SwarmStatusSnapshot

_WINDOW_SECONDS: Final[float]    = 60.0
_LOST_THRESHOLD: Final[float]    = 5.0
_EARTH_R_M: Final[float]         = 6_371_000.0

_THRESH_CRITICAL_C: Final[float] = 400.0
_THRESH_HIGH_C: Final[float]     = 280.0
_THRESH_MEDIUM_C: Final[float]   = 180.0

_LITRES_PER_M2: Final[dict[str, float]] = {
    "LOW": 0.5, "MEDIUM": 1.2, "HIGH": 2.5, "CRITICAL": 5.0
}


class TelemetryAggregator:
    """Ingests DroneTelemetry V2 packets and computes SwarmStatusSnapshot V2.
    Thread-safe - ingest() from MQTT callback; snapshot() from analysis loop.
    """


    def __init__(self, leader_id: str) -> None:
        """
        Initialises the aggregator for a given leader drone.

        Args:
            leader_id: Identifier of the leader drone.
        """
        self._leader_id      = leader_id
        self._lock           = threading.Lock()
        self._latest:        dict[str, DroneTelemetry] = {}
        self._history: defaultdict[str, deque[tuple[float, DroneTelemetry]]] = defaultdict(deque)
        self._fire_id:       str | None             = None
        self._fire_severity: str                       = "MEDIUM"

    def ingest(self, telem: DroneTelemetry) -> None:
        """
        Accepts a telemetry packet and records it in the sliding window.

        Args:
            telem: Incoming drone telemetry data.
        """
        with self._lock:
            self._latest[telem.drone_id] = telem
            dq = self._history[telem.drone_id]
            dq.append((telem.timestamp, telem))
            cutoff = time.time() - _WINDOW_SECONDS
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def set_fire_id(self, fire_id: str, severity: str = "MEDIUM") -> None:
        """
        Updates the active fire identifier and severity level.

        Args:
            fire_id: Active fire identifier.
            severity: Severity classification string.
        """
        with self._lock:
            self._fire_id      = fire_id
            self._fire_severity = severity

# SNAPSHOT GENERATION

    def snapshot(self) -> SwarmStatusSnapshot:
        """
        Computes and returns the latest swarm status snapshot from collected
        telemetry.

        Returns:
            A SwarmStatusSnapshot representing the current swarm state.
        """
        with self._lock:
            latest  = list(self._latest.values())
            history = {k: list(v) for k, v in self._history.items()}
            fire_id = self._fire_id
            _sev    = self._fire_severity

        if not latest:
            return SwarmStatusSnapshot(
                leader_id=self._leader_id,
                fire_id=fire_id,
                timestamp=time.time(),
                status="IDLE",
            )

        now_ts = time.time()

# Active / lost
        active      = [t for t in latest if t.connectivity != "LOST"]
        lost_drones = [t for t in latest if (now_ts - t.timestamp) > _LOST_THRESHOLD]

# Scouts (have thermal sensor) and fighters (have payload)
        scouts   = [t for t in active if t.thermal_peak_temp_c is not None]
        fighters = [t for t in active
                    if t.payload_litres is not None and t.payload_litres >= 0.0  # pyright: ignore[reportUnnecessaryComparison]
                    and t.task in ("SUPPRESSING", "RETURNING", "IDLE")]

# Battery (V2: Wh + pct)
        avg_battery_pct = (
            sum(t.battery_pct for t in active) / len(active) if active else 0.0
        )
        wh_vals        = [t.battery_wh for t in active if t.battery_wh is not None]  # pyright: ignore[reportUnnecessaryComparison]
        min_battery_wh = min(wh_vals) if wh_vals else 0.0

# Payload (V2: litres)
        pl_vals            = [t.payload_litres for t in fighters
                               if t.payload_litres is not None]  # pyright: ignore[reportUnnecessaryComparison]
        avg_payload_litres = sum(pl_vals) / len(pl_vals) if pl_vals else 0.0

# Total litres delivered (V2)
        total_litres = sum(
            t.litres_delivered for t in active
            if t.litres_delivered is not None
        )

# Fire intensity (V2: °C thresholds)
        fire_intensity = self._calc_fire_intensity(scouts)

# Perimeter (V2: Haversine metres)
        perimeter_m = self._best_perimeter(scouts)

# Spread rate (V2: Haversine centroid drift)
        spread_rate = self._calc_spread_rate(history, now_ts)

# Suppression pct (V2: litres vs area)
        suppression_pct = self._calc_suppression(total_litres, perimeter_m, fire_intensity)

# Wind (time-weighted average from scouts)
        wind_speed_mps, wind_direction_deg = self._calc_wind(scouts)

# Status
        suppressing = [t for t in fighters if t.task == "SUPPRESSING"]
        status      = self._derive_status(active, suppressing, fire_intensity)

        return SwarmStatusSnapshot(
            leader_id=self._leader_id,
            fire_id=fire_id,
            timestamp=time.time(),
            active_drones=len(active),
            lost_drones=len(lost_drones),
            avg_battery_pct=round(avg_battery_pct, 3),
            min_battery_wh=round(min_battery_wh, 1),
            avg_payload_litres=round(avg_payload_litres, 2),
            total_litres_delivered=round(total_litres, 2),
            perimeter_estimate_m=perimeter_m,
            suppression_pct=round(suppression_pct, 3) if suppression_pct is not None else None,
            fire_intensity=fire_intensity,  # pyright: ignore[reportArgumentType]
            status=status,  # pyright: ignore[reportArgumentType]
            spread_rate=spread_rate,  # pyright: ignore[reportArgumentType]
            wind_speed_mps=wind_speed_mps,
            wind_direction_deg=wind_direction_deg,
        )

    # PRIVATE - metric calculators

    def _calc_fire_intensity(self, scouts: list[DroneTelemetry]) -> str:
        """90th-percentile thermal peak across scouts severity string.
        V2 thresholds calibrated to Stefan-Boltzmann physics model.
        """
        temps = sorted(
            [t.thermal_peak_temp_c for t in scouts
             if t.thermal_peak_temp_c is not None],
            reverse=True,
        )
        if not temps:
            return "LOW"
        top_n = max(1, len(temps) // 10 or 1)
        peak  = sum(temps[:top_n]) / top_n
        if peak >= _THRESH_CRITICAL_C: return "CRITICAL"
        if peak >= _THRESH_HIGH_C:     return "HIGH"
        if peak >= _THRESH_MEDIUM_C:   return "MEDIUM"
        return "LOW"

    def _best_perimeter(self, scouts: list[DroneTelemetry]) -> float | None:
        """
        Best perimeter estimate (m):
        1. Weighted average of scout perimeter_estimate_m (from orbit+coverage model).
        2. Fallback: Haversine convex hull of scout GPS positions.
        """
        vals = [t.perimeter_estimate_m for t in scouts
                if t.perimeter_estimate_m is not None and t.perimeter_estimate_m > 0]
        if vals:
            return round(sum(vals) / len(vals), 1)

        gps = [(t.position[0], t.position[1]) for t in scouts]
        if len(gps) >= 3:
            return self._hull_perimeter_m(gps)
        return None

    def _hull_perimeter_m(self, pts: list[tuple[float, float]]) -> float | None:
        """Convex hull perimeter using Haversine distances (metres)."""
        hull = self._convex_hull(pts)
        if len(hull) < 2:
            return None
        total = 0.0
        for i in range(len(hull)):
            a = hull[i]
            b = hull[(i + 1) % len(hull)]
            total += _hav(a[0], a[1], b[0], b[1])
        return round(total, 1)

    def _convex_hull(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Jarvis march - O(nh), fine for n < 20 drones."""
        pts = list(set(pts))
        if len(pts) <= 1:
            return pts
        pivot = min(pts, key=lambda p: (p[1], p[0]))
        hull  = [pivot]
        while True:
            cur  = hull[-1]
            cand = pts[0]
            for p in pts[1:]:
                cross = ((cand[0]-cur[0])*(p[1]-cur[1])
                         - (cand[1]-cur[1])*(p[0]-cur[0]))
                if cross > 0 or (
                    cross == 0
                    and _hav(p[0],p[1],cur[0],cur[1])
                    > _hav(cand[0],cand[1],cur[0],cur[1])
                ):
                    cand = p
            if cand == hull[0]:
                break
            hull.append(cand)
        return hull

    def _calc_spread_rate(
        self, history: dict[str, list[tuple[float, DroneTelemetry]]], now: float
    ) -> str | None:
        """
        Thermal centroid drift over 60s window, measured in metres (Haversine).
        Wind-amplified: effective_drift = drift × (1 + wind/15).
        SLOW < 10m | MODERATE 10-50m | RAPID > 50m.
        """
        old_pts: list[tuple[float, float]] = []
        new_pts: list[tuple[float, float]] = []
        wind_mps = 0.0

        cutoff_old = now - _WINDOW_SECONDS
        cutoff_new = now - 10.0

        for _, records in history.items():
            for ts, t in records:
                if t.thermal_peak_temp_c is None:
                    continue
                lat, lon = t.position[0], t.position[1]
                if ts <= cutoff_old + 5:
                    old_pts.append((lat, lon))
                if ts >= cutoff_new:
                    new_pts.append((lat, lon))
                if t.wind_speed_mps is not None:
                    wind_mps = max(wind_mps, t.wind_speed_mps)

        if not old_pts or not new_pts:
            return None

        def centroid(ps: list[tuple[float, float]]) -> tuple[float, float]:
            return (sum(p[0] for p in ps)/len(ps), sum(p[1] for p in ps)/len(ps))

        oc = centroid(old_pts)
        nc = centroid(new_pts)
        drift_m   = _hav(oc[0], oc[1], nc[0], nc[1])
        effective = drift_m * (1.0 + wind_mps / 15.0)

        if effective < 10.0: return "SLOW"
        if effective < 50.0: return "MODERATE"
        return "RAPID"

    def _calc_suppression(
        self,
        total_litres:   float,
        perimeter_m:    float | None,
        fire_intensity: str,
    ) -> float | None:
        """
        eff = litres_delivered / litres_needed
        litres_needed = L_per_m² × area_m²
        area estimated from perimeter: A = P² / 4π (circular fire).
        """
        if perimeter_m is None or perimeter_m < 1.0:
            return None
        area_m2       = (perimeter_m ** 2) / (4 * math.pi)
        l_per_m2      = _LITRES_PER_M2.get(fire_intensity, 1.2)
        litres_needed = l_per_m2 * area_m2
        return min(1.0, total_litres / max(0.1, litres_needed))

    def _calc_wind(
        self, scouts: list[DroneTelemetry]
    ) -> tuple[float | None, float | None]:
        """
        Time-weighted average wind from scouts (exponential decay tau=10s).
        More recent readings contribute more.
        """
        speed_vals = [(t.wind_speed_mps, t.timestamp)
                      for t in scouts if t.wind_speed_mps is not None]
        dir_vals   = [(t.wind_direction_deg, t.timestamp)
                      for t in scouts if t.wind_direction_deg is not None]
        if not speed_vals:
            return (None, None)
        now = time.time()
        tau = 10.0

        def w_avg(vals: list[tuple[float, float]]) -> float | None:
            tw = tv = 0.0
            for v, ts in vals:
                w = math.exp(-(now - ts) / tau)
                tv += v * w; tw += w
            return tv / tw if tw > 0 else None

        ws = w_avg(speed_vals)
        wd = w_avg(dir_vals) if dir_vals else None
        return (
            round(ws, 2) if ws is not None else None,
            round(wd % 360, 1) if wd is not None else None,
        )

    def _derive_status(
        self,
        active:         list[DroneTelemetry],
        suppressing:    list[DroneTelemetry],
        fire_intensity: str,
    ) -> str:
        """Derives swarm status from active drone roles and fire intensity."""
        if not active:
            return "IDLE"
        returning = [t for t in active if t.task == "RETURNING"]
        if suppressing and fire_intensity in ("LOW", "MEDIUM"):
            return "CONTAINING"
        if suppressing:
            return "SUPPRESSING"
        if returning and not suppressing:
            return "WITHDRAWING"
        if any(t.task == "SCOUTING" for t in active):
            return "SCOUTING"
        return "ENGAGING"


def _hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance (metres)."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a    = (math.sin(dlat/2)**2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon/2)**2)
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))
