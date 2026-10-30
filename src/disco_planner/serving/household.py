"""Settlement type and household / life-stage features (2nd and 3rd order).

    zip  -> RUCA code (+ land area, metro distance) -> settlement type
    name -> age distribution (weak) --shrunk to national mix--> P(kids), P(homeowner), life stage
    zip climate x local month -> "cold season now" / "hot season now"

Every output keeps the inference chain in `source`, so the UI can show *why*.
Rates and multipliers live in config/household.yaml.
"""
from __future__ import annotations

import csv
from datetime import datetime
from functools import lru_cache

from disco_planner.data import catalog

DATA_DIR = catalog.DATA_DIR / "reference"


@lru_cache(maxsize=1)
def _ruca() -> dict[str, int]:
    with open(DATA_DIR / "ruca.csv", newline="") as f:
        return {r["zip"]: int(r["ruca"]) for r in csv.DictReader(f)}


def _cfg() -> dict:
    return catalog.config("household")


def settlement(zip5: str | None, land_area_class: str | None, metro_km: float | None) -> tuple[str | None, float, str]:
    """RUCA 1 metro core is split into urban core vs suburban using the zip's land-area class
    (small dense zips are cores); RUCA 2-10 map directly. Returns (type, confidence, chain)."""
    code = _ruca().get(zip5 or "")
    conf = _cfg()["settlement_confidence"]
    if code is None:
        return None, 0.0, "zip not in USDA RUCA 2020"
    if code == 1:
        core = land_area_class == "urban" or (metro_km is not None and metro_km < 8)
        kind = "urban_core" if core else "suburban"
        return kind, conf["ruca_1_split"], f"RUCA 1 (metro core) + land area '{land_area_class}' -> {kind}"
    kind = "suburban" if code in (2, 3) else "small_city" if code in (4, 5, 6) else "small_town" if code in (7, 8, 9) else "rural"
    return kind, conf["ruca_2_10"], f"RUCA {code} -> {kind}"


def _shrunk_age(age_dist: dict[str, float] | None, age_conf: float) -> dict[str, float]:
    national = _cfg()["national_adult_age_mix"]
    if not age_dist:
        return dict(national)
    return {b: age_conf * age_dist.get(b, 0.0) + (1 - age_conf) * national[b] for b in national}


def household_features(
    zip5: str | None, land_area_class: str | None, metro_km: float | None, zip_factor: float,
    age_dist: dict[str, float] | None, age_conf: float, publisher_id: str,
    climate_band: str | None, mountain: bool | None, near_ski: bool | None, local_dt: datetime,
) -> dict[str, dict]:
    """Returns {feature_name: {value|distribution, confidence, source}} (plain dicts so the caller
    can wrap them in its Feature model)."""
    cfg = _cfg()
    kind, s_conf, s_chain = settlement(zip5, land_area_class, metro_km)
    s_conf *= zip_factor  # billing 1.0 / unknown 0.7 / shipping 0.5 (DECISIONS D5)
    ages = _shrunk_age(age_dist, age_conf)
    host_mult = cfg["host_household_multipliers"].get(publisher_id, {})
    # Derived confidence: settlement carries most of the evidence; the (capped) age adds a little.
    derived_conf = round(s_conf * (0.4 + 0.6 * age_conf), 3) if kind else round(0.3 * age_conf, 3)

    def rate(table: str, mult_key: str) -> float:
        base = sum(ages[b] * cfg[table][b] for b in ages)
        m = cfg["settlement_multipliers"][mult_key].get(kind, 1.0) if kind else 1.0
        return round(min(0.95, base * m * host_mult.get(mult_key, 1.0)), 3)

    p_kids, p_home = rate("p_kids_by_age", "kids"), rate("p_homeowner_by_age", "homeowner")
    age_note = f"age from name (conf {age_conf:.2f}) shrunk to national mix"
    chain = f"{age_note} x {kind or 'unknown settlement'}" + (f" x host {host_mult}" if host_mult else "")

    # Life stage: P(family) from p_kids; the rest of each age bucket's mass goes to its default stage.
    no_kids = 1 - p_kids
    young = ages["18-24"] / max(1e-9, sum(ages.values()))
    stages = {
        "family_with_kids": p_kids,
        "young_adult": no_kids * young,
        "young_professional": no_kids * ages["25-34"] * (1.2 if kind == "urban_core" else 0.9),
        "established_no_kids": no_kids * (ages["35-44"] + ages["45-54"]),
        "empty_nester": no_kids * ages["55-64"],
        "retiree": no_kids * ages["65+"],
    }
    total = sum(stages.values())
    stages = {k: round(v / total, 3) for k, v in sorted(stages.items(), key=lambda kv: -kv[1])}

    car_dependent = 1.0 if kind in ("suburban", "small_city", "small_town", "rural") else 0.0
    outdoor = min(1.0, 0.5 * bool(mountain) + 0.3 * bool(near_ski) + 0.4 * (kind in ("rural", "small_town")))
    month = local_dt.month
    seasons = {
        name: float(climate_band in rule["bands"] and month in rule["months"]) for name, rule in cfg["seasons"].items()
    }
    out = {
        "settlement_type": {"value": kind, "confidence": round(s_conf, 3), "source": f"USDA RUCA 2020: {s_chain}"},
        "p_has_kids": {"value": p_kids, "confidence": derived_conf, "source": f"P(kids|age) x settlement: {chain}"},
        "p_homeowner": {"value": p_home, "confidence": derived_conf, "source": f"P(own|age) x settlement: {chain}"},
        "life_stage": {"distribution": stages, "confidence": derived_conf, "source": f"from p_has_kids + {age_note}"},
        "car_dependent": {"value": car_dependent, "confidence": round(s_conf * 0.8, 3), "source": f"settlement {kind}"},
        "outdoor_lifestyle": {"value": round(outdoor, 2), "confidence": round(s_conf * 0.6, 3),
                              "source": "mountain / near ski area / rural-small town"},
    }
    for name, value in seasons.items():
        out[name] = {"value": value, "confidence": round(0.6 * zip_factor if climate_band else 0.0, 3),
                     "source": f"climate band {climate_band} x local month {month}"}
    return out
