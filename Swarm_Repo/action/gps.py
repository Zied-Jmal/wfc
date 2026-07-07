"""
action/gps.py - WGS-84 GPS Mathematics
Real geodetic coordinate system for drone simulation.
All positions are stored in WGS-84 (lat_deg, lon_deg, alt_m_amsl).
Internal kinematics run in a local NED (North-East-Down) Cartesian frame
anchored at the home position. The two frames are converted on every tick
so that telemetry always emits proper GPS coordinates.
ISO units used throughout:
  lat / lon  : decimal degrees  (WGS-84)
  alt        : metres above mean sea level  (m AMSL)
  distance   : metres  (m)
  bearing    : degrees true (T), 0 = North, 90 = East
  velocity   : metres per second  (m/s)

Reference:
  Haversine  - Sinnott, R.W., "Virtues of the Haversine", Sky & Telescope, 1984
  NED frame  - NATO STANAG 4586 / ARP4754
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# WGS-84 ellipsoid constants
EARTH_RADIUS_M  = 6_371_000.0   # mean spherical radius (m) - sufficient for < 500 km
DEG_TO_RAD      = math.pi / 180.0
RAD_TO_DEG      = 180.0 / math.pi


@dataclass(frozen=True)
class GPSCoord:
    """Immutable WGS-84 coordinate.

    Attributes:
        lat_deg : latitude  in decimal degrees (-90 .. +90)
        lon_deg : longitude in decimal degrees (-180 .. +180)
        alt_m   : altitude above mean sea level in metres
    """

    lat_deg: float
    lon_deg: float
    alt_m:   float = 0.0

    def __repr__(self) -> str:
        ns = "N" if self.lat_deg >= 0 else "S"
        ew = "E" if self.lon_deg >= 0 else "W"
        return (f"GPSCoord({abs(self.lat_deg):.6f}{ns}, "
                f"{abs(self.lon_deg):.6f}{ew}, "
                f"{self.alt_m:.1f} m AMSL)")

    def as_tuple(self) -> tuple[float, float, float]:
        """(lat_deg, lon_deg, alt_m)"""
        return (self.lat_deg, self.lon_deg, self.alt_m)


def haversine_distance_m(a: GPSCoord, b: GPSCoord) -> float:
    """Great-circle distance between two WGS-84 points (metres).

    Ignores altitude difference - use 3-D distance for that.
    Accuracy: < 0.5% for distances up to ~1 000 km.
    """
    lat1 = a.lat_deg * DEG_TO_RAD
    lat2 = b.lat_deg * DEG_TO_RAD
    dlat = (b.lat_deg - a.lat_deg) * DEG_TO_RAD
    dlon = (b.lon_deg - a.lon_deg) * DEG_TO_RAD

    sin_dlat = math.sin(dlat / 2)
    sin_dlon = math.sin(dlon / 2)
    h = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def distance_3d_m(a: GPSCoord, b: GPSCoord) -> float:
    """Euclidean 3-D distance including altitude difference (metres)."""
    horiz = haversine_distance_m(a, b)
    dalt  = b.alt_m - a.alt_m
    return math.sqrt(horiz * horiz + dalt * dalt)


def initial_bearing_deg(a: GPSCoord, b: GPSCoord) -> float:
    """Initial bearing from a to b (degrees true, 0 = North, 90 = East).

    Uses forward azimuth formula.
    """
    lat1 = a.lat_deg * DEG_TO_RAD
    lat2 = b.lat_deg * DEG_TO_RAD
    dlon = (b.lon_deg - a.lon_deg) * DEG_TO_RAD
    x    = math.sin(dlon) * math.cos(lat2)
    y    = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.atan2(x, y) * RAD_TO_DEG) % 360.0


def destination_point(origin: GPSCoord, bearing_deg: float, distance_m: float) -> GPSCoord:
    """Compute destination GPSCoord given origin, bearing (T), and distance (m).

    Altitude is preserved from origin (caller adjusts alt separately).
    """
    ang_dist = distance_m / EARTH_RADIUS_M
    bear     = bearing_deg * DEG_TO_RAD
    lat1     = origin.lat_deg * DEG_TO_RAD
    lon1     = origin.lon_deg * DEG_TO_RAD

    lat2 = math.asin(
        math.sin(lat1) * math.cos(ang_dist)
        + math.cos(lat1) * math.sin(ang_dist) * math.cos(bear)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bear) * math.sin(ang_dist) * math.cos(lat1),
        math.cos(ang_dist) - math.sin(lat1) * math.sin(lat2),
    )
    return GPSCoord(lat2 * RAD_TO_DEG, (lon2 * RAD_TO_DEG + 540) % 360 - 180, origin.alt_m)


# NED GPS conversions (flat-earth approximation, valid < ~10 km)

def ned_to_gps(origin: GPSCoord, north_m: float, east_m: float, down_m: float) -> GPSCoord:
    """Convert NED offset (metres) from origin to WGS-84 coordinate.

    NED frame: North = +x, East = +y, Down = +z (altitude decreases).
    GPS  frame: lat increases North, lon increases East, alt increases Up.

    Valid for offsets up to ~10 km from origin (< 0.01% error).
    """
    # 1 degree of latitude ~ 111 320 m everywhere
    # 1 degree of longitude ~ 111 320 * cos(lat) m
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lon = 111_320.0 * math.cos(origin.lat_deg * DEG_TO_RAD)

    lat = origin.lat_deg + north_m / metres_per_deg_lat
    lon = origin.lon_deg + east_m  / metres_per_deg_lon
    alt = origin.alt_m  - down_m   # NED down is positive downward

    return GPSCoord(lat, lon, alt)


def gps_to_ned(origin: GPSCoord, point: GPSCoord) -> tuple[float, float, float]:
    """Convert GPSCoord to NED offset (metres) from origin.

    Returns (north_m, east_m, down_m).
    """
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lon = 111_320.0 * math.cos(origin.lat_deg * DEG_TO_RAD)
    north_m = (point.lat_deg - origin.lat_deg) * metres_per_deg_lat
    east_m = (point.lon_deg - origin.lon_deg) * metres_per_deg_lon
    down_m = -(point.alt_m - origin.alt_m) # altitude up NED down negative
    return (north_m, east_m, down_m)


def add_gps_noise(coord: GPSCoord, sigma_h_m: float = 1.5, sigma_v_m: float = 2.5) -> GPSCoord:
    """Add realistic GPS receiver noise to a coordinate.

    Consumer-grade GPS:  sigma_h ~ 1.5 m, sigma_v ~ 2.5 m  (CEP 50%)
    RTK-corrected GPS:   sigma_h ~ 0.02 m, sigma_v ~ 0.05 m
    Default simulates a typical UBlox M8N receiver (no RTK).

    Uses Box-Muller transform for Gaussian noise (no scipy dependency).
    """
    import random as _r
    # Box-Muller: convert uniform to Gaussian
    def _gauss(sigma: float) -> float:
        u1 = max(1e-10, _r.random())
        u2 = _r.random()
        z  = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        return z * sigma

    metres_per_deg_lat = 111_320.0
    metres_per_deg_lon = 111_320.0 * math.cos(coord.lat_deg * DEG_TO_RAD)

    noisy_lat = coord.lat_deg + _gauss(sigma_h_m) / metres_per_deg_lat
    noisy_lon = coord.lon_deg + _gauss(sigma_h_m) / metres_per_deg_lon
    noisy_alt = coord.alt_m   + _gauss(sigma_v_m)

    return GPSCoord(noisy_lat, noisy_lon, noisy_alt)
