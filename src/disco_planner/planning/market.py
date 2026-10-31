"""The simulated market: what it costs to win page views on each host (D24).

Disco would read this from its auction logs. Here we build it from the other campaigns we know about
(the 15 demo plans + 100 holdout plans), each bidding a benchmark-anchored CPC:
    rival bid at host = category CPC x price-tier multiplier x relative fit x fixed advertiser spread
and per page view each rival's bid varies log-normally (pacing, shopper-level adjustments).

For a campaign with click rate `ctr` at a host, a page view is won by bidding at least
    needed = max(top rival's eCPM, reserve eCPM) / (ctr x 1000)      (one slot, second price)
and the second price paid IS that needed amount. Sorting the needed bids over sampled page views gives
the host's bid landscape: to win a share w of its page views you bid the w-quantile, and you pay the
average of the cheaper ones. Buying more of a host costs more per unit: that's the diminishing return.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from functools import lru_cache

from disco_planner.data import catalog


def _cfg() -> dict:
    return catalog.config("pricing")


def _spread(key: str) -> float:
    """Fixed per-advertiser multiplier in [1 - s, 1 + s], deterministic from the brief."""
    s = _cfg()["market"]["advertiser_spread"]
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return 1 - s + 2 * s * h


def value_cpc(category: str | None, price_tier: str, fit: float, key: str) -> float:
    m = _cfg()["market"]
    base = m["cpc_by_category"].get(category or "", m["cpc_by_category"]["default"])
    return base * m["price_tier_multiplier"].get(price_tier, 1.0) * fit * _spread(key)


def relative_fit(score: float, top: float) -> float:
    m = _cfg()["priors"]["relative_fit_multiplier"]
    return m["min"] + (m["max"] - m["min"]) * (score / top if top else 0)


def primary_category(relevant_tags: list[dict], recommended_hosts: list[str]) -> str | None:
    categories = {p.category for p in catalog.publishers()}
    for t in sorted(relevant_tags, key=lambda t: -t["weight"]):
        if t["tag"] in categories:
            return t["tag"]
    if recommended_hosts:
        return catalog.publisher_by_id()[recommended_hosts[0]].category
    return None


@lru_cache(maxsize=1)
def book() -> dict[str, list[dict]]:
    """host_id -> rivals [{campaign, bid, ctr}], with bids calibrated so clearing prices match benchmarks."""
    raw = _raw_book()
    reference_ctr = _cfg()["priors"]["ctr"]["mid"]
    clearing = sorted(p for host in raw for p in _landscape(raw, host, reference_ctr, None))
    kappa = _cfg()["market"]["target_median_clearing_cpc"] / quantile(clearing, 0.5) if clearing else 1.0
    return {host: [{**r, "bid": r["bid"] * kappa} for r in rivals] for host, rivals in raw.items()}


def _raw_book() -> dict[str, list[dict]]:
    """host_id -> rivals [{campaign, bid, ctr}] from every known plan that is live on that host."""
    folders = [catalog.REPO_ROOT / "demo_cache" / "plans", catalog.REPO_ROOT / "reports" / "holdout_plans"]
    ctr_mid = _cfg()["priors"]["ctr"]["mid"]
    out: dict[str, list[dict]] = {}
    for folder in folders:
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            plan = json.loads(path.read_text())
            if plan.get("profile", {}).get("gate") not in ("proceed", "clarify"):
                continue
            recs = [s for s in plan["publishers"] if s.get("recommended")]
            if not recs:
                continue
            top = max(s["score"] for s in recs)
            cat = primary_category(plan["profile"].get("relevant_tags", []), [s["publisher_id"] for s in recs])
            tier = plan["profile"].get("price_tier", "unknown")
            for s in recs:
                fit = relative_fit(s["score"], top)
                out.setdefault(s["publisher_id"], []).append({
                    "campaign": plan["brief"], "bid": value_cpc(cat, tier, fit, plan["brief"]), "ctr": ctr_mid * fit,
                })
    return out


def landscape(publisher_id: str, own_ctr: float, exclude_brief: str | None = None) -> list[float]:
    """Sorted bids needed to win each sampled page view at this host (= the second price paid if won)."""
    return _landscape(book(), publisher_id, own_ctr, exclude_brief)


def _landscape(the_book: dict, publisher_id: str, own_ctr: float, exclude_brief: str | None) -> list[float]:
    m = _cfg()["market"]
    reserve = catalog.config("serving")["auction"]["reserve_ecpm"]
    rivals = [r for r in the_book.get(publisher_id, []) if r["campaign"] != exclude_brief]
    rng = random.Random(f"{publisher_id}|{exclude_brief}")  # deterministic per host and campaign
    needed = []
    for _ in range(m["samples_per_host"]):
        top = max((r["bid"] * math.exp(rng.gauss(0, m["per_view_sigma"])) * r["ctr"] * 1000 for r in rivals), default=0.0)
        needed.append(max(top, reserve) / (own_ctr * 1000) + 0.01)
    return sorted(needed)


def quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    i = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[i]
