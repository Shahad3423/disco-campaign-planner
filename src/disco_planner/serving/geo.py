"""Location helpers: haversine distance, nearest-point lookups, climate rules.

All geography beyond the Census zip centroid comes from small hand-written tables in
data/reference/ (coast_points.csv, metros.csv, ski_areas.csv; coordinates are approximate,
from public gazetteer knowledge). Everything here is approximate and used as soft priors.
"""
import csv
import math
from functools import lru_cache
from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parents[3] / "data" / "reference"
EARTH_RADIUS_KM = 6371.0

COAST_KM = 50          # is_coastal if closer than this to a coast reference point
SKI_KM = 150           # near_ski_area
METRO_SAME_DAY_KM = 40
METRO_NEXT_DAY_KM = 100

# Appalachian ridge reference points (a mountain zip is within APPALACHIAN_KM of one).
APPALACHIAN_AXIS = [(34.9, -84.0), (35.6, -83.5), (35.8, -82.3), (37.5, -79.5), (38.9, -79.9),
                    (41.2, -76.5), (41.9, -74.4), (42.4, -73.2), (44.1, -72.8), (44.2, -71.5),
                    (44.1, -74.2), (45.0, -70.5)]
APPALACHIAN_KM = 70

COLD_STATES = {"ME", "NH", "VT", "NY", "MI", "WI", "MN", "ND", "SD", "AK",
               "MT", "WY", "CO", "UT", "ID"}
HOT_HUMID_STATES = {"FL", "LA", "MS", "AL", "GA", "SC", "TX", "AR", "HI", "PR", "VI"}
ARID_STATES = {"AZ", "NV", "NM"}
HURRICANE_STATES = {"TX", "LA", "MS", "AL", "FL", "GA", "SC", "NC"}
HURRICANE_KM = 80


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@lru_cache(maxsize=None)
def _load_points(filename: str) -> list[tuple[str, float, float]]:
    with open(REFERENCE_DIR / filename, newline="") as handle:
        return [(r["name"], float(r["lat"]), float(r["lon"])) for r in csv.DictReader(handle)]


def nearest(filename: str, lat: float, lon: float) -> tuple[str, float]:
    """Name and distance (km) of the closest point in a reference table."""
    return min(((name, haversine_km(lat, lon, plat, plon))
                for name, plat, plon in _load_points(filename)), key=lambda pair: pair[1])


def is_mountain(state: str, lat: float, lon: float) -> bool:
    """Rough Rockies / Sierra / Cascades boxes plus distance to the Appalachian axis."""
    rockies_south = 35.5 <= lat < 42 and -114.5 <= lon <= -105.4
    rockies_north = 42 <= lat <= 48.9 and -116.5 <= lon <= -109.0
    sierra = state in {"CA", "NV"} and 35.5 <= lat <= 40.5 and -121.5 <= lon <= -117.5
    cascades = state in {"OR", "WA"} and 42 <= lat <= 49 and -122.5 <= lon <= -120.8
    if rockies_south or rockies_north or sierra or cascades:
        return True
    return any(haversine_km(lat, lon, alat, alon) <= APPALACHIAN_KM
               for alat, alon in APPALACHIAN_AXIS)


def climate_band(state: str, lat: float, lon: float) -> str:
    """Approximate climate band from state and coordinates (first matching rule wins)."""
    if state in {"OR", "WA"} and lon < -121.5:
        return "marine_pacific_nw"
    if state in ARID_STATES or (state == "CA" and lat < 36 and lon > -117.5) \
            or (state == "TX" and lon < -102.5):
        return "arid_desert"
    if state in HOT_HUMID_STATES:
        return "hot_humid_sunbelt"
    if state == "NY" and lat < 41.8:  # NYC / Long Island are temperate, upstate is snow belt
        return "temperate"
    if state in COLD_STATES or lat >= 43.5:
        return "cold_snow_belt"
    return "temperate"
