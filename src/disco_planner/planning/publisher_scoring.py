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
    """Relevance from tag matches, with a tag hierarchy:
    - specific subcategory match (e.g. "bedding"): full weight
    - category-only match (e.g. "apparel"): capped, because every apparel host has it
    - attribute tags (e.g. "women", "sustainable"): small bonus, can't create a match alone
    """
    cfg = catalog.config("scoring")["category"]
    generic = set(catalog.config("scoring")["generic_tags"])
    categories = {p.category for p in catalog.publishers()}
    matched = [(t.weight, t.tag) for t in profile.relevant_tags if t.tag in pub.tags]
    if not matched:
        return 0.0, "no category link"
    specific = sorted(((w, t) for w, t in matched if t not in generic and t not in categories), reverse=True)
    broad = [(w, t) for w, t in matched if t in categories]
    attrs = [(w, t) for w, t in matched if t in generic]

    broad_base = min(cfg["category_only_cap"], max(w for w, _ in broad)) if broad else 0.0
    if specific and specific[0][0] >= broad_base:
        base, kind = specific[0][0], "specific"
    elif broad:
        base, kind = broad_base, "category only" if not specific else "category, weak specific"
    else:
        base, kind = min(cfg["attribute_only_cap"], max(w for w, _ in attrs)), "attribute tags only"
    bonus = cfg["extra_specific_bonus"] * sum(w for w, _ in specific[1:]) + cfg["attribute_bonus"] * sum(
        w for w, _ in attrs if kind != "attribute tags only"
    )
    detail = f"matches {', '.join(t for _, t in matched)} ({kind})"
    return min(1.0, base + bonus), detail


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


def persona_fit(pub: Publisher, weights: dict[str, float]) -> tuple[float, str]:
    """Two-hop: advertiser -> plausible personas -> how present they are on this host.
    Catches what tags miss, e.g. an age-agnostic activewear brief vs a 50-70 host."""
    from disco_planner.planning.personas import host_fit  # local import: personas imports this module's types

    if not weights:
        return NEUTRAL, "no plausible personas yet"
    by_id = catalog.persona_by_id()
    fits = {pid: host_fit(by_id[pid], pub) for pid in weights}
    value = sum(weights[pid] * fits[pid] for pid in weights) / sum(weights.values())
    top = max(weights, key=lambda pid: weights[pid] * fits[pid])
    return value, f"reaches {by_id[top].name} best (host fit {fits[top]:.2f})"


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


def score_publisher(
    profile: AdvertiserProfile, pub: Publisher, today: date, weights: dict[str, float] | None = None,
) -> PublisherScore:
    cfg = catalog.config("scoring")
    w = cfg["quality_weights"]
    cat, cat_d = category_fit(profile, pub)
    aud, aud_d = audience_fit(profile, pub)
    price, price_d = price_fit(profile, pub)
    notes, notes_d, alignment = notes_fit(profile, pub)
    pfit, pfit_d = persona_fit(pub, weights or {})
    scale, scale_d = scale_fit(pub)
    season, season_d = seasonality_fit(profile, pub, today)

    components = [
        ScoreComponent(name="category_fit", value=cat, weight=1.0, detail=cat_d + " (multiplier)"),
        ScoreComponent(name="audience_fit", value=aud, weight=w["audience_fit"], detail=aud_d),
        ScoreComponent(name="price_fit", value=price, weight=1.0, detail=price_d + f" (multiplier ^{cfg['price_exponent']})"),
        ScoreComponent(name="persona_fit", value=pfit, weight=w["persona_fit"], detail=pfit_d),
        ScoreComponent(name="notes_fit", value=notes, weight=w["notes_fit"], detail=notes_d),
        ScoreComponent(name="scale", value=scale, weight=w["scale"], detail=scale_d),
        ScoreComponent(name="seasonality", value=season, weight=w["seasonality"], detail=season_d),
    ]
    # score = relevance x affordability x quality (DECISIONS D8, "matching v2"):
    # relevance ~ will they click, affordability ~ will they buy, quality ~ everything else.
    exponent = cfg["price_exponent"]
    quality = sum(c.value * c.weight for c in components if c.name not in ("category_fit", "price_fit"))
    score = cat * price**exponent * quality

    flags = []
    competing = sorted(pub.tags & set(profile.competing_tags))
    if competing:
        # Soft penalty: scaled by how many intake samples agreed this is a competitor (default 1.0).
        share = max(profile.competing_tag_share.get(t, 1.0) for t in competing)
        score *= 1 - (1 - cfg["penalties"]["competitor_conflict"]) * share
        flags.append(f"competitor_conflict: host sells {', '.join(competing)} ({share:.0%} of samples agree); may block the ad")
    if alignment == -1:
        score *= cfg["penalties"]["notes_misaligned"]
        flags.append(f"audience_note_conflict: {notes_d}")

    return PublisherScore(
        publisher_id=pub.id, publisher_name=pub.name, score=round(score, 4),
        components=components, flags=flags, recommended=False, reason="",
    )


def rank_publishers(profile: AdvertiserProfile, today: date) -> list[PublisherScore]:
    """Score every host, then decide recommend vs exclude with a human-readable reason."""
    from disco_planner.planning.personas import persona_weights

    rec = catalog.config("scoring")["recommend"]
    weights = persona_weights(profile)
    scores = sorted(
        (score_publisher(profile, p, today, weights) for p in catalog.publishers()),
        key=lambda s: s.score, reverse=True,
    )

    if profile.gate in ("off_network", "reject_low_signal"):
        for s in scores:
            s.reason = f"Not recommended: {profile.gate_reason}"
        return scores

    cutoff = max(rec["min_score"], rec["relative_to_top"] * scores[0].score)
    # If no host matches a specific subcategory, the network has no strong home for this
    # product: say so and propose a small test on a few hosts instead of a full spread.
    def is_strong(s: PublisherScore) -> bool:
        cat, _, price = s.components[0], s.components[1], next(c for c in s.components if c.name == "price_fit")
        return "(specific)" in cat.detail and price.value >= rec["strong_min_price_fit"]

    strong = any(is_strong(s) for s in scores if s.score >= cutoff)
    max_picks = rec["max_publishers"] if strong else rec["weak_fit_max_publishers"]
    picked = 0
    for s in scores:
        cat = next(c for c in s.components if c.name == "category_fit")
        if cat.value < rec["min_category_fit"]:
            s.reason = f"Excluded: {cat.detail} for this product."
        elif s.score < cutoff:
            weakest = min(s.components[:3], key=lambda c: c.value)  # among relevance, audience, price
            s.reason = f"Excluded: score {s.score:.2f} below cutoff {cutoff:.2f}; weakest signal {weakest.name} ({weakest.detail})."
        elif picked >= max_picks:
            s.reason = "Excluded: viable, but below the top picks; kept as an exploration candidate."
        else:
            s.recommended = True
            picked += 1
            best_quality = max(s.components[1:], key=lambda c: c.value * c.weight)
            s.reason = f"Recommended: {s.components[0].detail}; strongest supporting signal {best_quality.name} ({best_quality.detail})."
            if not strong:
                s.flags.append("weak_network_fit: no host both sells this subcategory and has shoppers at this price; treat as a small test")
        if s.flags:
            s.reason += " Flags: " + " | ".join(s.flags)
    return scores
