"""Serve-time shopper features from three signals: publisher, zip code, first name.

Every inferred feature is a `Feature` carrying a value (or a distribution), a
`confidence` between 0 and 1, and a `source` saying where it came from. Confidence 0
means "we know nothing", and the value is then an uninformative default. Nothing here
raises on bad input: junk in, confidence 0 out (see docs/DECISIONS.md D3, D4, D5).
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

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
AGE_BUCKETS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]

# ---- Tunable constants ------------------------------------------------------

# Geo confidence is multiplied by this, depending on whose zip we were given (D5).
# Billing = the buyer's own area. Shipping may be a gift recipient's area.
ZIP_SOURCE_FACTOR = {"billing": 1.0, "shipping": 0.5, "unknown": 0.7}
# Base confidence of each zip-derived feature when the zip is valid and billing.
STATE_CONF, REGION_CONF, TIMEZONE_CONF = 0.95, 0.9, 0.85  # split states make tz approximate
LOCAL_HOUR_CONF, URBANICITY_CONF, COLD_CONF = 0.85, 0.5, 0.4
# Names: the age distribution is wide, so its confidence is capped (low-medium, D4).
AGE_CONF_CAP = 0.6
NAME_COUNT_FOR_FULL_TRUST = 5000  # names rarer than this get proportionally less trust

# Gift probability = baseline + bumps. Deliberately low confidence (D4, 3rd order).
GIFT_BASELINE, GIFT_SEASON_BUMP, GIFT_PUBLISHER_BUMP, GIFT_SHIPPING_BUMP = 0.05, 0.10, 0.15, 0.05
GIFT_MONTHS = (11, 12)
GIFT_CONF = 0.3

# Cold / mountain: a coarse state-level guess, plus any zip at latitude >= 42.
MOUNTAIN_STATES = {"CO", "UT", "WY", "MT", "ID", "NM"}
COLD_STATES = {"ME", "NH", "VT", "NY", "MI", "WI", "MN", "ND", "SD", "AK"}
COLD_LATITUDE = 42.0

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


def _unknown_geo() -> dict[str, Feature]:
    none = Feature(source="none")
    return {"state": none, "region": none, "timezone": none, "local_hour": none,
            "urbanicity": none, "cold_or_mountain": Feature(value=False, source="none")}


def _geo_features(signals: ShopperSignals, now_utc: datetime) -> dict[str, Feature]:
    """State, region, timezone, local hour, urbanicity and cold flag from the zip."""
    row = _zips().get(_clean_zip(signals.zip) or "")
    if row is None:
        return _unknown_geo()

    factor = ZIP_SOURCE_FACTOR[signals.zip_source]
    source = f"census_zcta_{signals.zip_source}_zip"

    def feature(value, base_conf):
        return Feature(value=value, confidence=round(base_conf * factor, 3), source=source)

    local_hour = now_utc.astimezone(ZoneInfo(row["timezone"])).hour
    is_cold = (row["state"] in COLD_STATES or row["state"] in MOUNTAIN_STATES
               or row["lat"] >= COLD_LATITUDE)
    return {
        "state": feature(row["state"], STATE_CONF),
        "region": feature(row["region"] or None, REGION_CONF if row["region"] else 0.0),
        "timezone": feature(row["timezone"], TIMEZONE_CONF),
        "local_hour": feature(float(local_hour), LOCAL_HOUR_CONF),
        "urbanicity": feature(row["urbanicity"], URBANICITY_CONF),
        "cold_or_mountain": feature(is_cold, COLD_CONF),
    }


# ---- Publisher-derived features ---------------------------------------------

def _cart_prior(publisher: dict | None) -> CartPrior:
    if publisher is None:
        return CartPrior()
    return CartPrior(category=publisher["category"],
                     subcategories=publisher["subcategories"],
                     avg_order_value_usd=publisher["avg_order_value_usd"],
                     confidence=0.6, source="publisher_catalog")


def _gift_signal(signals: ShopperSignals, publisher: dict | None, now_utc: datetime) -> Feature:
    probability = GIFT_BASELINE
    if now_utc.month in GIFT_MONTHS:
        probability += GIFT_SEASON_BUMP
    if publisher is not None and "gifting" in publisher["subcategories"]:
        probability += GIFT_PUBLISHER_BUMP
    if signals.zip_source == "shipping":  # shipping zip is more often a recipient's (D5)
        probability += GIFT_SHIPPING_BUMP
    return Feature(value=round(probability, 3), confidence=GIFT_CONF,
                   source="season+publisher+zip_source")


# ---- Public entry point -----------------------------------------------------

def build_features(signals: ShopperSignals, now_utc: datetime) -> ShopperFeatures:
    """Turn the three serve-time signals into features. Never raises on bad input."""
    if now_utc.tzinfo is None:  # treat naive datetimes as UTC
        now_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))
    publisher = _publishers().get(signals.publisher_id)
    name_valid, display_name, p_female, age_bucket = _name_features(signals.first_name)
    geo = _geo_features(signals, now_utc)
    return ShopperFeatures(
        name_valid=name_valid,
        display_name=display_name,
        p_female=p_female,
        age_bucket=age_bucket,
        state=geo["state"],
        region=geo["region"],
        timezone=geo["timezone"],
        local_hour=geo["local_hour"],
        urbanicity=geo["urbanicity"],
        # ACS income was unavailable when building the tables (no API key): always unknown.
        income_tier=Feature(source="none"),
        cart_prior=_cart_prior(publisher),
        gift_signal=_gift_signal(signals, publisher, now_utc),
        cold_or_mountain=geo["cold_or_mountain"],
    )
