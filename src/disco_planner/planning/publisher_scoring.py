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
from disco_planner.planning import taxonomy

def _shape() -> dict:
    return catalog.config("scoring")["shape"]


# Missing information is LEFT OUT, not filled in (D25): a component the brief gives no evidence for
# returns None, and the quality average is taken over the components we do know (weights renormalised).


def _age_range(text: str | None) -> tuple[int, int] | None:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def category_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
    """Topic link: how close the advertiser's product tags are to what the host sells."""
    if catalog.config("scoring")["category"]["method"] == "tree":
        return tree_category_fit(profile, pub)
    return tag_category_fit(profile, pub)


def tree_category_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
    """D27: best over (advertiser tag, host product) pairs of  tag weight x tree similarity. No branches, no bonuses.
    Facet tags (subscription, women, sustainable...) are not products and never create a topic link."""
    cfg = catalog.config("scoring")["category"]
    host_nodes = taxonomy.host_products(pub)
    adv = [t for t in profile.relevant_tags if taxonomy.is_product(t.tag)]
    if cfg["specific_only"]:  # a broad tag listed next to its own children adds nothing (same rule as for hosts)
        ancestors = {a for t in adv for a in taxonomy.path(t.tag)[1:]}
        adv = [t for t in adv if t.tag not in ancestors]
    best, why = 0.0, "no product in common with the host's range"
    for t in adv:
        for h in sorted(host_nodes):
            sim = taxonomy.similarity(t.tag, h) ** cfg["similarity_power"]
            if t.weight * sim > best:
                best = t.weight * sim
                why = (f"'{t.tag}' (weight {t.weight:g}) vs host '{h}': similarity {sim:.2f} -> {best:.2f}"
                       + ("" if t.tag != h else " (same product)"))
    return best, why


def facet_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float | None, str]:
    """Share (by weight) of the advertiser's stated facets that the host also has. None if none stated or method=tags."""
    if catalog.config("scoring")["category"]["method"] != "tree":
        return None, "facets not used by this method"
    stated = [(t.weight, t.tag) for t in profile.relevant_tags if taxonomy.is_facet(t.tag)]
    if not stated:
        return None, "no facets stated (left out)"
    have = taxonomy.host_facets(pub)
    matched = [(w, t) for w, t in stated if t in have]
    value = sum(w for w, _ in matched) / sum(w for w, _ in stated)
    return value, (f"host has {', '.join(t for _, t in matched) or 'none'} of the stated facets "
                   f"({', '.join(t for _, t in stated)})")


def tag_category_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str]:
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


def audience_density(profile: AdvertiserProfile, pub: Publisher) -> tuple[float | None, str]:
    """Share of this host's shoppers who are in the advertiser's STATED audience (D26):
    P(gender) x P(age in range), assuming the two are independent. Unlike audience_fit (does the host's age range
    COVER my range?), this is a density: a broad host range covers any narrow target but is mostly out of it."""
    parts, details = [], []
    if profile.target_gender in ("female", "male"):
        share = pub.audience.gender_split.get(profile.target_gender, 0.0)
        parts.append(share)
        details.append(f"{share:.0%} {profile.target_gender}")
    target, host = _age_range(profile.target_age_range), _age_range(pub.audience.age_skew)
    if target and host:
        overlap = max(0, min(target[1], host[1]) - max(target[0], host[0]))
        parts.append(overlap / max(1, host[1] - host[0]))
        details.append(f"{overlap / max(1, host[1] - host[0]):.0%} of ages {pub.audience.age_skew} inside {profile.target_age_range}")
    if not parts:
        return None, "audience not stated"
    density = 1.0
    for part in parts:
        density *= part
    return density, " x ".join(details)


def relevance(profile: AdvertiserProfile, pub: Publisher, topic: float, topic_detail: str) -> tuple[float, str, str]:
    """Relevance = the larger of the topic link and a discounted audience density, when the advertiser stated an age
    (one broad demographic proves nothing) and enough of the host's shoppers are in it. Returns (value, detail, source)."""
    cfg = catalog.config("scoring")["relevance"]
    density, density_d = audience_density(profile, pub)
    if density is not None and cfg["audience_requires_age"] and not _age_range(profile.target_age_range):
        density = None
    audience_led = cfg["audience_only_discount"] * density if density is not None and density >= cfg["audience_evidence_min"] else 0.0
    if audience_led > topic:
        return audience_led, (f"audience-led: {density:.0%} of this host's shoppers are in your stated audience ({density_d}); "
                              f"x {cfg['audience_only_discount']:g} = {audience_led:.2f}; topic link {topic:.2f} ({topic_detail})"), "audience"
    return topic, topic_detail, ("topic" if topic > 0 else "none")


def audience_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float | None, str]:
    """Average of the parts the advertiser actually stated (gender share, age overlap); None if neither."""
    parts, details = [], []
    if profile.target_gender in ("female", "male"):
        share = pub.audience.gender_split.get(profile.target_gender, 0.0)
        parts.append(share)
        details.append(f"{share:.0%} {profile.target_gender}")
    target, host = _age_range(profile.target_age_range), _age_range(pub.audience.age_skew)
    if target and host:
        overlap = max(0, min(target[1], host[1]) - max(target[0], host[0]))
        parts.append(overlap / max(1, target[1] - target[0]))
        details.append(f"age {pub.audience.age_skew} vs target {profile.target_age_range}")
    if not parts:
        return None, "audience not stated (left out)"
    return sum(parts) / len(parts), "; ".join(details)


def price_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float | None, str]:
    """One-sided: a shopper whose typical order is at least the product's price can afford it (1.0);
    only a product that costs MORE than the host's typical order is penalised. None if no price info."""
    cfg = catalog.config("scoring")
    tiers = cfg["income_tiers"]
    shape = _shape()
    score, details = None, []
    if profile.price_point_usd:
        ratio = profile.price_point_usd / pub.avg_order_value_usd
        score = 1.0 if ratio <= 1 else math.exp(-math.log(ratio) / shape["price_spread_above"])
        details.append(f"price ${profile.price_point_usd:.0f} vs host AOV ${pub.avg_order_value_usd:.0f}"
                       + (" (affordable)" if ratio <= 1 else f" ({ratio:.1f}x the typical order)"))
    needed = cfg["price_tier_to_income"].get(profile.price_tier)
    if needed and pub.audience.income_tier in tiers:
        gap = tiers.index(needed) - tiers.index(pub.audience.income_tier)
        score = 1.0 if score is None else score
        if gap > 0:
            score *= shape["income_gap_penalty"] ** gap
            details.append(f"host income {pub.audience.income_tier} below {needed} needed for {profile.price_tier}")
        elif not profile.price_point_usd:
            details.append(f"host income {pub.audience.income_tier} suits {profile.price_tier}")
    if score is None:
        return None, "price not stated (left out)"
    return score, "; ".join(details)


def notes_fit(profile: AdvertiserProfile, pub: Publisher) -> tuple[float, str, int]:
    values = _shape()["notes_values"]
    for s in profile.note_signals:
        if s.publisher_id == pub.id:
            key = {1: "aligned", 0: "neutral", -1: "misaligned"}[s.alignment]
            return values[key], s.why, s.alignment
    return values["neutral"], "no read on notes", 0


def persona_fit(pub: Publisher, weights: dict[str, float]) -> tuple[float, str]:
    """Two-hop: advertiser -> plausible personas -> how present they are on this host.
    Catches what tags miss, e.g. an age-agnostic activewear brief vs a 50-70 host."""
    from disco_planner.planning.personas import host_fit  # local import: personas imports this module's types

    if not weights:
        return None, "no plausible personas (left out)"
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
    v = _shape()["seasonality_values"]
    giftable_product = profile.purchase_model == "gift" or any(t.tag == "gifting" for t in profile.relevant_tags)
    giftable_host = "gifting" in pub.tags or "gift" in pub.notes.lower()
    if not giftable_product:
        return None, "not a gifting product (left out)"
    in_season = any(
        0 <= (date(today.year, m, 1) - today).days <= cfg["lookahead_days"] or today.month == m
        for m in cfg["season_months"]
    )
    if giftable_host and in_season:
        return v["in_season"], "gifting host, gifting season imminent"
    if giftable_host:
        return v["off_season"], "gifting host, off-season"
    return v["non_gift_host"], "host not gifting-oriented"


def power_mean(parts: list[tuple[float, float]], p: float, eps: float = 1e-3) -> float:
    """Weighted power mean of (value, weight) pairs, weights normalised to sum 1.
    p=1 arithmetic, p=0 geometric (limit), p<0 increasingly 'all must be good' (-> minimum)."""
    total = sum(w for _, w in parts) or 1.0
    parts = [(max(v, eps), w / total) for v, w in parts]
    if abs(p) < 1e-9:
        return math.exp(sum(w * math.log(v) for v, w in parts))
    return sum(w * v**p for v, w in parts) ** (1 / p)


def combine(components: list[ScoreComponent], relevance_value: float) -> float:
    """score = relevance^a x price^b x M_p(quality parts), with unknown parts left out."""
    cfg = catalog.config("scoring")
    price = next(c.value for c in components if c.name == "price_fit")
    quality_parts = [(c.value, c.weight) for c in components
                     if c.name not in ("category_fit", "price_fit") and c.value is not None]  # known parts only
    quality = power_mean(quality_parts, cfg["quality_p"]) if quality_parts else 1.0
    price_term = price ** cfg["price_exponent"] if price is not None else 1.0  # unknown price: no information
    return relevance_value ** cfg["relevance_exponent"] * price_term * quality


def score_publisher(
    profile: AdvertiserProfile, pub: Publisher, today: date, weights: dict[str, float] | None = None,
) -> PublisherScore:
    cfg = catalog.config("scoring")
    w = cfg["quality_weights"]
    topic, topic_d = category_fit(profile, pub)
    aud, aud_d = audience_fit(profile, pub)
    cat, cat_d, rel_source = relevance(profile, pub, topic, topic_d)
    price, price_d = price_fit(profile, pub)
    notes, notes_d, alignment = notes_fit(profile, pub)
    pfit, pfit_d = persona_fit(pub, weights or {})
    scale, scale_d = scale_fit(pub)
    season, season_d = seasonality_fit(profile, pub, today)
    facet, facet_d = facet_fit(profile, pub)

    components = [
        ScoreComponent(name="category_fit", value=cat, weight=1.0, detail=cat_d + " (multiplier)"),
        ScoreComponent(name="audience_fit", value=aud, weight=w["audience_fit"], detail=aud_d),
        ScoreComponent(name="price_fit", value=price, weight=1.0,
                       detail=price_d + (f" (multiplier ^{cfg['price_exponent']})" if price is not None else "")),
        ScoreComponent(name="persona_fit", value=pfit, weight=w["persona_fit"], detail=pfit_d),
        ScoreComponent(name="notes_fit", value=notes, weight=w["notes_fit"], detail=notes_d),
        ScoreComponent(name="scale", value=scale, weight=w["scale"], detail=scale_d),
        ScoreComponent(name="seasonality", value=season, weight=w["seasonality"], detail=season_d),
        ScoreComponent(name="facet_fit", value=facet, weight=w["facet_fit"], detail=facet_d),
    ]
    # score = relevance^a x affordability^b x M_p(quality)  (docs/SCORING.md):
    # relevance ~ will they click, affordability ~ will they buy, quality ~ everything else.
    score = combine(components, cat)

    flags = []
    competing = sorted(pub.tags & set(profile.competing_tags))
    if competing:
        # Flag only: whether a host blocks a competing brand is the host's setting, enforced at
        # serving (DECISIONS D21). It's rare network-wide (~6% of advertiser-host pairs), so it
        # must not distort fit; the plan just tells the advertiser this host may need to opt in.
        share = max(profile.competing_tag_share.get(t, 1.0) for t in competing)
        flags.append(f"competitor_conflict: host also sells {', '.join(competing)} ({share:.0%} of samples agree); "
                     "it may need to opt in to show a competing brand")
    if alignment == -1:
        score *= cfg["penalties"]["notes_misaligned"]
        flags.append(f"audience_note_conflict: {notes_d}")

    return PublisherScore(
        publisher_id=pub.id, publisher_name=pub.name, score=round(score, 4),
        components=components, flags=flags, recommended=False, reason="", relevance_source=rel_source,
    )


def _component(s: PublisherScore, name: str) -> float:
    """A component's value; 0.0 when it was left out (no evidence cannot satisfy an evidence requirement)."""
    value = next(c.value for c in s.components if c.name == name)
    return 0.0 if value is None else value


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

    # Anchor the relative cutoff to the best host we can count on running: the best NON-competing
    # host with a real category link. A competitor may refuse (host setting, D21), so its score must not
    # raise the bar for everyone else. Competitors are still listed when they clear this bar.
    is_comp = lambda s: any(f.startswith("competitor_conflict") for f in s.flags)
    anchor = next((s for s in scores if not is_comp(s) and s.relevance_source == "topic"
                   and s.components[0].value >= rec["min_category_fit"]), scores[0])
    cutoff = max(rec["min_score"], rec["relative_to_top"] * anchor.score)
    # If no host matches a specific subcategory, the network has no strong home for this
    # product: say so and propose a small test on a few hosts instead of a full spread.
    tree = catalog.config("scoring")["category"]["method"] == "tree"

    def is_strong(s: PublisherScore) -> bool:
        cat, price = s.components[0], next(c for c in s.components if c.name == "price_fit")
        close_topic = (s.relevance_source == "topic" and cat.value >= rec["strong_min_relevance"]) if tree else "(specific)" in cat.detail
        return close_topic and (price.value is None or price.value >= rec["strong_min_price_fit"])

    def weak_topic(s: PublisherScore) -> bool:
        cat = s.components[0]
        return (s.relevance_source == "topic" and cat.value < rec["weak_topic_below"]) if tree else "(category only)" in cat.detail

    strong = any(is_strong(s) for s in scores if s.score >= cutoff)
    max_picks = rec["max_publishers"] if strong else rec["weak_fit_max_publishers"]
    picked = 0
    for s in scores:
        cat = next(c for c in s.components if c.name == "category_fit")
        if cat.value < rec["min_category_fit"]:
            s.reason = f"Excluded: {cat.detail} for this product."
        elif weak_topic(s) and _component(s, "persona_fit") < rec["category_only_min_persona_fit"]:
            s.reason = (f"Excluded: only a weak topic link ({cat.detail}) and the shoppers this product is for "
                        f"are rare here (persona fit {_component(s, 'persona_fit'):.2f}).")
        elif s.score < cutoff:
            weakest = min((c for c in s.components[:3] if c.value is not None), key=lambda c: c.value)
            s.reason = f"Excluded: score {s.score:.2f} below cutoff {cutoff:.2f}; weakest signal {weakest.name} ({weakest.detail})."
        elif picked >= max_picks:
            s.reason = "Excluded: viable, but below the top picks; kept as an exploration candidate."
        else:
            s.recommended = True
            picked += 1
            best_quality = max((c for c in s.components[1:] if c.value is not None and c.name != "price_fit"),
                               key=lambda c: c.value * c.weight, default=s.components[0])
            s.reason = f"Recommended: {s.components[0].detail}; strongest supporting signal {best_quality.name} ({best_quality.detail})."
            if not strong:
                s.flags.append("weak_network_fit: no host both sells this subcategory and has shoppers at this price; treat as a small test")
        if s.flags:
            s.reason += " Flags: " + " | ".join(s.flags)

    mark_exploration(scores, picked > 0)
    return scores


def mark_exploration(scores: list[PublisherScore], has_plan: bool) -> None:
    """Pick the best EXCLUDED hosts as a small paid test (D26), so outcomes exist for pairs we'd otherwise never
    observe. Ranked as if their relevance were at least the floor; skips competitor-flagged hosts and hosts whose
    notes conflict with the brief."""
    cfg = catalog.config("scoring")["exploration"]
    if not has_plan or cfg["max_hosts"] <= 0:
        return
    candidates = []
    for s in scores:
        if s.recommended or any(f.startswith(("competitor_conflict", "audience_note_conflict")) for f in s.flags):
            continue
        candidates.append((combine(s.components, max(s.components[0].value, cfg["relevance_floor"])), s))
    for floor_score, s in sorted(candidates, key=lambda x: -x[0])[: cfg["max_hosts"]]:
        s.exploration = True
        s.reason = (s.reason.split(" Flags:")[0] + f" Exploration test: would score {floor_score:.2f} with a relevance of "
                    f"{cfg['relevance_floor']:g}; gets a small separate test budget.")
