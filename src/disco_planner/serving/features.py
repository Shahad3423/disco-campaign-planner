"""Serve-time shopper features from three signals: publisher, zip code, first name.

Every inferred feature is a `Feature` carrying a value (or a distribution), a
`confidence` between 0 and 1, and a `source` saying where it came from. Confidence 0
means "we know nothing", and the value is then an uninformative default. Nothing here
raises on bad input: junk in, confidence 0 out (see docs/DECISIONS.md D3, D4, D5).

Inferred gender and age are SOFT PRIORS ONLY: they are never referenced in ad copy and
never used for hard exclusion (D5 guardrail 3).
"""
import csv
import json
import math
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from disco_planner.serving import calendar, geo
from disco_planner.serving.household import household_features

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
AGE_BUCKETS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]

# ---- Tunable constants ------------------------------------------------------

# Geo confidence is multiplied by this, depending on whose zip we were given (D5).
# Billing = the buyer's own area. Shipping may be a gift recipient's area.
ZIP_SOURCE_FACTOR = {"billing": 1.0, "shipping": 0.5, "unknown": 0.7}
# Base confidence of each zip-derived feature when the zip is valid and billing.
STATE_CONF, REGION_CONF, TIMEZONE_CONF = 0.95, 0.9, 0.85  # split states make tz approximate
LOCAL_HOUR_CONF, URBANICITY_CONF, COLD_CONF = 0.85, 0.5, 0.4
CLIMATE_CONF, NEARBY_CONF, INCOME_CONF = 0.6, 0.5, 0.5   # approximate lookups
CALENDAR_CONF = 0.9
# Names are a WEAK age signal: the distribution is wide, SSA counts are births not survivors
# (biased old), and the data ends in 2017. So age confidence is capped very low.
AGE_CONF_CAP = 0.15
NAME_COUNT_FOR_FULL_TRUST = 5000  # names rarer than this get proportionally less trust

# Gift probability = baseline + calendar intensity + giftable-publisher bump + shipping bump.
# Deliberately low confidence (D4, 3rd order).
GIFT_BASELINE, GIFT_CALENDAR_WEIGHT, GIFT_PUBLISHER_BUMP, GIFT_SHIPPING_BUMP = 0.05, 0.25, 0.15, 0.05
GIFT_CONF = 0.3

BAD_NAMES = {
    "test", "testing", "asdf", "asdfgh", "qwerty", "qwe", "abc", "xyz", "none", "null",
    "nan", "na", "unknown", "guest", "admin", "user", "name", "firstname", "first",
    "customer", "anonymous", "fake", "foo", "bar", "baz", "aaa", "xxx", "lol", "hello",
}
PROFANITY = {"fuck", "shit", "bitch", "asshole", "bastard", "dick", "cunt", "slut",
             "whore", "piss", "cock", "nazi", "damn"}
MAX_NAME_LENGTH = 20


# ---- Models -----------------------------------------------------------------

class ShopperSignals(BaseModel):
    """The only three things we get at serve time (plus how the zip was obtained)."""
    publisher_id: str
    zip: str = ""
    first_name: str = ""
    zip_source: Literal["billing", "shipping", "unknown"] = "unknown"


class Feature(BaseModel):
    """One inferred feature: a value or a distribution, with confidence and source."""
    value: str | float | bool | None = None
    distribution: dict[str, float] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "none"


class CartPrior(BaseModel):
    """What the shopper probably bought, from the publisher's catalog entry."""
    category: str | None = None
    subcategories: list[str] = []
    avg_order_value_usd: float | None = None
    confidence: float = 0.0
    source: str = "none"


class ShopperFeatures(BaseModel):
    name_valid: Feature
    display_name: Feature          # value None -> creatives use no-name copy
    p_female: Feature
    age_bucket: Feature            # distribution over AGE_BUCKETS
    state: Feature
    region: Feature
    timezone: Feature
    local_hour: Feature
    urbanicity: Feature
    income_tier: Feature
    cart_prior: CartPrior
    gift_signal: Feature
    cold_or_mountain: Feature
    # Geography beyond state/region (all approximate)
    climate_band: Feature
    mountain: Feature
    coast_distance_km: Feature
    is_coastal: Feature
    hurricane_coast: Feature
    sun_belt: Feature
    nearest_metro: Feature
    metro_distance_km: Feature
    urbanicity_proxy: Feature
    ski_distance_km: Feature
    near_ski_area: Feature
    delivery_speed_proxy: Feature
    # Time and calendar (shopper local time, UTC if the zip is unknown)
    day_of_week: Feature
    is_weekend: Feature
    daypart: Feature
    active_moments: Feature        # distribution {moment name: intensity}
    # Settlement + household / life stage (serving/household.py, config/household.yaml)
    settlement_type: Feature       # urban_core / suburban / small_city / small_town / rural (USDA RUCA)
    p_has_kids: Feature
    p_homeowner: Feature
    life_stage: Feature            # distribution over life stages
    car_dependent: Feature
    outdoor_lifestyle: Feature
    cold_season_now: Feature
    hot_season_now: Feature


# ---- Reference data (loaded lazily, once) -----------------------------------

@lru_cache(maxsize=1)
def _names() -> dict[str, dict]:
    """names.csv as {name: row}; numbers are converted from text."""
    table = {}
    with open(DATA_DIR / "reference" / "names.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            table[row["name"]] = {k: float(v) for k, v in row.items() if k != "name"}
    return table


@lru_cache(maxsize=1)
def _zips() -> dict[str, dict]:
    """zips.csv as {zip: row}; lat/lon/area become floats."""
    table = {}
    with open(DATA_DIR / "reference" / "zips.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            for key in ("lat", "lon", "land_sqmi"):
                row[key] = float(row[key])
            table[row["zip"]] = row
    return table


@lru_cache(maxsize=1)
def _publishers() -> dict[str, dict]:
    with open(DATA_DIR / "publishers.json") as handle:
        return {p["id"]: p for p in json.load(handle)}


# ---- Name handling ----------------------------------------------------------

def _title_case(name: str) -> str:
    """JOHN -> John, mary-kate -> Mary-Kate, o'neil -> O'Neil. Mixed case is left alone."""
    if not (name.isupper() or name.islower()):
        return name  # McDonald, DeShawn: the user typed it deliberately
    # Capitalize each letter run: "mary-kate" -> "Mary-Kate", "o'neil" -> "O'Neil".
    return re.sub(r"[A-Za-z\u00C0-\u024F]+", lambda m: m.group().capitalize(), name)


def sanitize_name(raw: str) -> str | None:
    """Return a safe display name, or None if the input looks like junk."""
    name = (raw or "").strip()
    name = name.split()[0] if name else ""  # first word only
    if not (2 <= len(name) <= MAX_NAME_LENGTH):
        return None
    if not all(ch.isalpha() or ch in "-'" for ch in name):  # digits, emails, symbols
        return None
    if not name[0].isalpha():
        return None
    lowered = name.lower()
    if lowered in BAD_NAMES or any(word in lowered for word in PROFANITY):
        return None
    if len(set(lowered)) == 1:  # "aaaa"
        return None
    return _title_case(name)


def _lookup_name_row(display_name: str) -> dict | None:
    """Find the name in the SSA table; for 'Mary-Kate' try 'mary-kate' then 'mary'."""
    key = display_name.lower()
    return _names().get(key) or _names().get(key.split("-")[0])


def _name_features(raw_name: str) -> tuple[Feature, Feature, Feature, Feature]:
    """Return (name_valid, display_name, p_female, age_bucket)."""
    display = sanitize_name(raw_name)
    if display is None:
        flat = {b: 1 / len(AGE_BUCKETS) for b in AGE_BUCKETS}
        return (Feature(value=False, confidence=0.9, source="name_rules"),
                Feature(value=None, confidence=0.9, source="name_rules"),
                Feature(value=0.5, source="none"),
                Feature(distribution=flat, source="none"))

    valid = Feature(value=True, confidence=0.9, source="name_rules")
    shown = Feature(value=display, confidence=0.9, source="name_rules")
    row = _lookup_name_row(display)
    if row is None:  # a plausible name that is rare in the SSA data
        flat = {b: 1 / len(AGE_BUCKETS) for b in AGE_BUCKETS}
        return valid, shown, Feature(value=0.5, source="none"), Feature(distribution=flat, source="none")

    trust = min(1.0, row["n_total"] / NAME_COUNT_FOR_FULL_TRUST)
    p_female = row["p_female"]
    # Confidence is high when the name is lopsided (0.99 or 0.01), 0 when 50/50.
    gender_conf = abs(2 * p_female - 1) * trust
    ages = {b: row[b] for b in AGE_BUCKETS}
    # Confidence from how peaked the age distribution is (1 - normalized entropy).
    entropy = -sum(p * math.log(p) for p in ages.values() if p > 0)
    age_conf = min(AGE_CONF_CAP, (1 - entropy / math.log(len(AGE_BUCKETS))) * trust)
    return (valid, shown,
            Feature(value=p_female, confidence=round(gender_conf, 3), source="ssa_babynames"),
            Feature(distribution=ages, confidence=round(age_conf, 3), source="ssa_babynames"))


# ---- Zip handling -----------------------------------------------------------

def _clean_zip(raw: str) -> str | None:
    """Keep the first 5 digits of a zip (drops ZIP+4); None if malformed."""
    text = (raw or "").strip()[:5]
    return text if len(text) == 5 and text.isdigit() else None


GEO_FEATURE_NAMES = [
    "state", "region", "timezone", "local_hour", "urbanicity", "cold_or_mountain",
    "climate_band", "mountain", "coast_distance_km", "is_coastal", "hurricane_coast",
    "sun_belt", "nearest_metro", "metro_distance_km", "urbanicity_proxy", "ski_distance_km",
    "near_ski_area", "delivery_speed_proxy", "income_tier"]


def _unknown_geo() -> dict[str, Feature]:
    """Every zip-derived feature with confidence 0 (bad or missing zip)."""
    return {name: Feature(source="none") for name in GEO_FEATURE_NAMES}


def _urbanicity_proxy(area_class: str, metro_km: float) -> str:
    """Combine the zip's own urbanicity with distance to a big metro."""
    points = {"urban": 2, "suburban": 1, "rural": 0}[area_class]
    points += 1 if metro_km < 30 else (0 if metro_km < 80 else -1)
    return "urban" if points >= 3 else ("suburban" if points >= 1 else "rural")


def _delivery_speed(urbanicity: str, metro_km: float) -> str:
    if urbanicity == "urban" or metro_km < geo.METRO_SAME_DAY_KM:
        return "same_or_next_day_likely"
    if metro_km < geo.METRO_NEXT_DAY_KM:
        return "next_day_likely"
    return "standard"


def _geo_features(signals: ShopperSignals) -> dict[str, Feature]:
    """Everything derived from the zip: state, climate, coast, metro, ski, delivery, income."""
    row = _zips().get(_clean_zip(signals.zip) or "")
    if row is None:
        return _unknown_geo()

    factor = ZIP_SOURCE_FACTOR[signals.zip_source]
    source = f"census_zcta_{signals.zip_source}_zip"
    lat, lon, state = row["lat"], row["lon"], row["state"]

    def feature(value, base_conf, src=source):
        return Feature(value=value, confidence=round(base_conf * factor, 3), source=src)

    band = geo.climate_band(state, lat, lon)
    mountain = geo.is_mountain(state, lat, lon)
    coast_km = geo.nearest("coast_points.csv", lat, lon)[1]
    metro_name, metro_km = geo.nearest("metros.csv", lat, lon)
    ski_km = geo.nearest("ski_areas.csv", lat, lon)[1]
    is_coastal = coast_km < geo.COAST_KM
    sun_belt = band in ("hot_humid_sunbelt", "arid_desert") or (state == "CA" and lat < 35.5)
    hurricane = state in geo.HURRICANE_STATES and coast_km < geo.HURRICANE_KM
    income = row.get("income_tier") or None  # column only exists when built with a Census key
    approx = "approx_" + source

    return {
        "state": feature(state, STATE_CONF),
        "region": feature(row["region"] or None, REGION_CONF if row["region"] else 0.0),
        "timezone": feature(row["timezone"], TIMEZONE_CONF),
        "local_hour": feature(None, LOCAL_HOUR_CONF),  # value filled in by build_features
        "urbanicity": feature(row["urbanicity"], URBANICITY_CONF),
        "cold_or_mountain": feature(band == "cold_snow_belt" or mountain, COLD_CONF, approx),
        "climate_band": feature(band, CLIMATE_CONF, approx),
        "mountain": feature(mountain, CLIMATE_CONF, approx),
        "coast_distance_km": feature(round(coast_km, 1), NEARBY_CONF, approx),
        "is_coastal": feature(is_coastal, NEARBY_CONF, approx),
        "hurricane_coast": feature(hurricane, CLIMATE_CONF, approx),
        "sun_belt": feature(sun_belt, CLIMATE_CONF, approx),
        "nearest_metro": feature(metro_name, NEARBY_CONF, approx),
        "metro_distance_km": feature(round(metro_km, 1), NEARBY_CONF, approx),
        "urbanicity_proxy": feature(_urbanicity_proxy(row["urbanicity"], metro_km), NEARBY_CONF, approx),
        "ski_distance_km": feature(round(ski_km, 1), NEARBY_CONF, approx),
        "near_ski_area": feature(ski_km < geo.SKI_KM, NEARBY_CONF, approx),
        "delivery_speed_proxy": feature(_delivery_speed(row["urbanicity"], metro_km), 0.4, approx),
        "income_tier": feature(income, INCOME_CONF if income else 0.0, "census_acs_2022"),
    }


# ---- Publisher-derived features ---------------------------------------------

def _cart_prior(publisher: dict | None) -> CartPrior:
    if publisher is None:
        return CartPrior()
    return CartPrior(category=publisher["category"],
                     subcategories=publisher["subcategories"],
                     avg_order_value_usd=publisher["avg_order_value_usd"],
                     confidence=0.6, source="publisher_catalog")


def _gift_signal(signals: ShopperSignals, publisher: dict | None,
                 moments: list[calendar.Moment]) -> Feature:
    """Gift probability driven by the retail calendar (gifting moments), not fixed months."""
    gift_intensity = max((m.intensity for m in moments if m.gifting), default=0.0)
    probability = GIFT_BASELINE + GIFT_CALENDAR_WEIGHT * gift_intensity
    if publisher is not None and "gifting" in publisher["subcategories"]:
        probability += GIFT_PUBLISHER_BUMP
    if signals.zip_source == "shipping":  # shipping zip is more often a recipient's (D5)
        probability += GIFT_SHIPPING_BUMP
    return Feature(value=round(probability, 3), confidence=GIFT_CONF,
                   source="calendar+publisher+zip_source")


# ---- Public entry point -----------------------------------------------------

def build_features(signals: ShopperSignals, now_utc: datetime) -> ShopperFeatures:
    """Turn the three serve-time signals into features. Never raises on bad input."""
    if now_utc.tzinfo is None:  # treat naive datetimes as UTC
        now_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))
    publisher = _publishers().get(signals.publisher_id)
    name_valid, display_name, p_female, age_bucket = _name_features(signals.first_name)
    geo_features = _geo_features(signals)

    # Shopper local time via the zip's timezone; fall back to UTC (confidence 0).
    timezone_feature = geo_features["timezone"]
    if timezone_feature.value:
        local_dt = now_utc.astimezone(ZoneInfo(timezone_feature.value))
        time_conf, time_source = geo_features["local_hour"].confidence, timezone_feature.source
        geo_features["local_hour"] = Feature(value=float(local_dt.hour),
                                             confidence=time_conf, source=time_source)
    else:
        local_dt, time_conf, time_source = now_utc, 0.0, "utc_fallback"
    time_info = calendar.time_features(local_dt)

    def time_feature(value):
        return Feature(value=value, confidence=time_conf, source=time_source)

    moments = calendar.active_moments(local_dt)

    zip5 = _clean_zip(signals.zip)
    zip_row = _zips().get(zip5 or "")
    household = household_features(
        zip5=zip5 if zip_row else None,
        land_area_class=zip_row["urbanicity"] if zip_row else None,
        metro_km=geo_features["metro_distance_km"].value if zip_row else None,
        zip_factor=ZIP_SOURCE_FACTOR[signals.zip_source] if zip_row else 0.0,
        age_dist=age_bucket.distribution, age_conf=age_bucket.confidence,
        publisher_id=signals.publisher_id,
        climate_band=geo_features["climate_band"].value, mountain=geo_features["mountain"].value,
        near_ski=geo_features["near_ski_area"].value, local_dt=local_dt,
    )
    return ShopperFeatures(
        **{name: Feature(**values) for name, values in household.items()},
        name_valid=name_valid,
        display_name=display_name,
        p_female=p_female,
        age_bucket=age_bucket,
        cart_prior=_cart_prior(publisher),
        gift_signal=_gift_signal(signals, publisher, moments),
        day_of_week=time_feature(time_info["day_of_week"]),
        is_weekend=time_feature(time_info["is_weekend"]),
        daypart=time_feature(time_info["daypart"]),
        active_moments=Feature(distribution={m.name: m.intensity for m in moments},
                               confidence=CALENDAR_CONF * (1.0 if time_conf else 0.7),
                               source="config/calendar.yaml"),
        **geo_features,
    )
