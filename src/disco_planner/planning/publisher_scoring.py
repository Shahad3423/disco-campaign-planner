"""Deterministic publisher scoring (DECISIONS D8).

The LLM's job ended at AdvertiserProfile; from here everything is plain arithmetic
with weights from config/scoring.yaml, so every rank has a visible breakdown.
"""
from __future__ import annotations

import math
import re
from datetime import date

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserProfile, Publisher, PublisherScore, ScoreComponent

NEUTRAL = 0.6  # used when the advertiser didn't tell us: mild, not zero, and labelled "not stated"


def _age_range(text: str | None) -> tuple[int, int] | None:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def category_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
    """Strongest matching tag, plus a little credit for each additional match."""
    matches = sorted(
        ((t.weight, t.tag) for t in profile.relevant_tags if t.tag in pub.tags), reverse=True
    )
    if not matches:
        return 0.0, "no category link"
    cfg = catalog.config("scoring")
    generic = set(cfg["generic_tags"])
    specific = [(w, t) for w, t in matches if t not in generic]
    detail = "matches " + ", ".join(t for _, t in matches)
    if not specific:
        return min(cfg["generic_only_cap"], matches[0][0]), detail + " (attribute tags only)"
    best = specific[0][0]
    extra = 0.15 * sum(w for w, t in matches if (w, t) != specific[0])
    return min(1.0, best + extra), detail


def audience_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
    parts, details = [], []
    if profile.target_gender in ("female", "male"):
        share = pub.audience.gender_split.get(profile.target_gender, 0.0)
        parts.append(share)
        details.append(f"{share:.0%} {profile.target_gender}")
    else:
        parts.append(NEUTRAL)
        details.append("gender not stated")

    target, host = _age_range(profile.target_age_range), _age_range(pub.audience.age_skew)
    if target and host:
        overlap = max(0, min(target[1], host[1]) - max(target[0], host[0]))
        parts.append(overlap / max(1, target[1] - target[0]))
        details.append(f"age {pub.audience.age_skew} vs target {profile.target_age_range}")
    else:
        parts.append(NEUTRAL)
        details.append("age not stated")
    return sum(parts) / len(parts), "; ".join(details)


def price_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
    """Asymmetric: a $650 product on a $28-AOV host is worse than a $20 product on a $150 host."""
    cfg = catalog.config("scoring")
    tiers = cfg["income_tiers"]
    score, details = NEUTRAL, []

    if profile.price_point_usd:
        ratio = profile.price_point_usd / pub.avg_order_value_usd
        spread = 1.5 if ratio > 1 else 2.5
        score = math.exp(-abs(math.log(ratio)) / spread)
        details.append(f"price ${profile.price_point_usd:.0f} vs host AOV ${pub.avg_order_value_usd:.0f}")

    needed = cfg["price_tier_to_income"].get(profile.price_tier)
    if needed and pub.audience.income_tier in tiers:
        gap = tiers.index(needed) - tiers.index(pub.audience.income_tier)
        if gap > 0:
            score *= 0.7**gap
            details.append(f"host income {pub.audience.income_tier} below {needed} needed for {profile.price_tier}")
        elif not profile.price_point_usd:
            score = 0.8
            details.append(f"host income {pub.audience.income_tier} suits {profile.price_tier}")
    if not details:
        details.append("price not stated")
    return score, "; ".join(details)


def notes_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str, int]:
    for s in profile.note_signals:
        if s.publisher_id == pub.id:
            return (s.alignment + 1) / 2, s.why, s.alignment
    return 0.5, "no read on notes", 0


def scale_fit(pub: Publisher) -> tuple[float, str]:
    sizes = [math.log10(p.monthly_impressions) for p in catalog.publishers()]
    lo, hi = min(sizes), max(sizes)
    return (math.log10(pub.monthly_impressions) - lo) / (hi - lo), f"{pub.monthly_impressions / 1e6:.1f}M monthly impressions"


def seasonality_fit(profile: AdvertiserProfile, pub: Publisher, today: date) -> tuple[float, str]:
    cfg = catalog.config("scoring")["gifting"]
    giftable_product = profile.purchase_model == "gift" or any(t.tag == "gifting" for t in profile.relevant_tags)
    giftable_host = "gifting" in pub.tags or "gift" in pub.notes.lower()
    if not giftable_product:
        return 0.5, "not a gifting product"
    in_season = any(
        0 <= (date(today.year, m, 1) - today).days <= cfg["lookahead_days"] or today.month == m
        for m in cfg["season_months"]
    )
    if giftable_host and in_season:
        return 1.0, "gifting host, gifting season imminent"
    if giftable_host:
        return 0.75, "gifting host, off-season"
    return 0.3, "host not gifting-oriented"


def score_publisher(profile: AdvertiserProfile, pub: Publisher, today: date) -> PublisherScore:
    cfg = catalog.config("scoring")
    w = cfg["quality_weights"]
    cat, cat_d = category_fit(profile, pub)
    aud, aud_d = audience_fit(profile, pub)
    price, price_d = price_fit(profile, pub)
    notes, notes_d, alignment = notes_fit(profile, pub)
    scale, scale_d = scale_fit(pub)
    season, season_d = seasonality_fit(profile, pub, today)

    components = [
        ScoreComponent(name="category_fit", value=cat, weight=1.0, detail=cat_d + " (multiplier)"),
        ScoreComponent(name="audience_fit", value=aud, weight=w["audience_fit"], detail=aud_d),
        ScoreComponent(name="price_fit", value=price, weight=w["price_fit"], detail=price_d),
        ScoreComponent(name="notes_fit", value=notes, weight=w["notes_fit"], detail=notes_d),
        ScoreComponent(name="scale", value=scale, weight=w["scale"], detail=scale_d),
        ScoreComponent(name="seasonality", value=season, weight=w["seasonality"], detail=season_d),
    ]
    quality = sum(c.value * c.weight for c in components[1:])
    score = cat * quality

    flags = []
    competing = sorted(pub.tags & set(profile.competing_tags))
    if competing:
        score *= cfg["penalties"]["competitor_conflict"]
        flags.append(f"competitor_conflict: host sells {', '.join(competing)}; may block the ad")
    if alignment == -1:
        score *= cfg["penalties"]["notes_misaligned"]
        flags.append(f"audience_note_conflict: {notes_d}")

    return PublisherScore(
        publisher_id=pub.id, publisher_name=pub.name, score=round(score, 4),
        components=components, flags=flags, recommended=False, reason="",
    )


def rank_publishers(profile: AdvertiserProfile, today: date) -> list[PublisherScore]:
    """Score every host, then decide recommend vs exclude with a human-readable reason."""
    rec = catalog.config("scoring")["recommend"]
    scores = sorted(
        (score_publisher(profile, p, today) for p in catalog.publishers()),
        key=lambda s: s.score, reverse=True,
    )

    if profile.gate in ("off_network", "reject_low_signal"):
        for s in scores:
            s.reason = f"Not recommended: {profile.gate_reason}"
        return scores

    cutoff = max(rec["min_score"], rec["relative_to_top"] * scores[0].score)
    picked = 0
    for s in scores:
        cat = next(c for c in s.components if c.name == "category_fit")
        if cat.value < rec["min_category_fit"]:
            s.reason = f"Excluded: {cat.detail} for this product."
        elif s.score < cutoff:
            weakest = min(s.components[:3], key=lambda c: c.value)  # among the three fit signals
            s.reason = f"Excluded: score {s.score:.2f} below cutoff {cutoff:.2f}; weakest signal {weakest.name} ({weakest.detail})."
        elif picked >= rec["max_publishers"]:
            s.reason = "Excluded: viable, but below the top picks; kept as an exploration candidate."
        else:
            s.recommended = True
            picked += 1
            best_quality = max(s.components[1:], key=lambda c: c.value * c.weight)
            s.reason = f"Recommended: {s.components[0].detail}; strongest supporting signal {best_quality.name} ({best_quality.detail})."
        if s.flags:
            s.reason += " Flags: " + " | ".join(s.flags)
    return scores
