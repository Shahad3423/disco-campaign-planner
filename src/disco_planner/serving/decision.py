"""Stage 2: choose which offers to show one shopper on one host, in milliseconds, no LLM.

    signals (publisher_id, zip, first_name) -> features -> persona posterior
    -> eligible campaigns -> expected eCPM -> creative variant -> rendered copy

Production notes: features are table lookups; campaigns and creatives come pre-generated
from planning; budget pacing and frequency caps would be checked against a low-latency
counter store before the eCPM ranking (not simulated here).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from disco_planner.data import catalog
from disco_planner.planning.budget import relative_fit
from disco_planner.planning.personas import _overlap, _range, host_fit
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.features import ShopperFeatures, ShopperSignals, build_features

PERSONA_FEMALE_SHARE = {"female": 0.9, "female-leaning": 0.7, "balanced": 0.5, "male-leaning": 0.3, "male": 0.1}
AGE_BUCKET_RANGES = {"18-24": (18, 24), "25-34": (25, 34), "35-44": (35, 44), "45-54": (45, 54), "55-64": (55, 64), "65+": (65, 80)}


class Offer(BaseModel):
    campaign: str
    publisher_id: str
    persona_id: str
    headline: str
    body: str
    cta: str
    pricing_model: str
    bid: float                     # final bid for THIS impression (after bid strategy)
    bid_breakdown: str = ""
    expected_ctr: float
    ecpm: float
    reasons: list[str]


class ServeDecision(BaseModel):
    persona_posterior: dict[str, float]
    uncertain: bool
    offers: list[Offer]
    # top offer, flattened for simple renderers
    headline: str | None = None
    body: str | None = None
    cta: str | None = None
    name_used: bool = False
    reasons: list[str] = Field(default_factory=list)


def _cfg() -> dict:
    return catalog.config("serving")


def _temper(raw: float, confidence: float) -> float:
    return (1 - confidence) + confidence * raw


def persona_posterior(
    features: ShopperFeatures, publisher_id: str, use_name: bool = True, use_household: bool = True,
) -> tuple[dict[str, float], list[str]]:
    """P(persona | host, name, zip, time). Prior = how well each persona fits the host's audience."""
    cfg = _cfg()["posterior"]
    pub = catalog.publisher_by_id()[publisher_id]
    household_cfg = catalog.config("household")
    notes = []
    scores = {}
    for persona in catalog.personas():
        prior = host_fit(persona, pub)
        like = 1.0
        if use_name and features.p_female.value is not None:
            # Bayes: P(name reads female | persona) = pf*q + (1-pf)*(1-q), q = persona's share of women
            pf, q = float(features.p_female.value), PERSONA_FEMALE_SHARE.get(persona.gender_skew, 0.5)
            like *= _temper((pf * q + (1 - pf) * (1 - q)) / 0.5, features.p_female.confidence)
        if use_name and features.age_bucket.distribution:
            # P(age bucket | persona) ~ overlap of bucket with persona's age range; +0.4 smoothing
            # so a persona is never ruled out by a weak signal (age confidence is capped at 0.15).
            span = _range(persona.age_range)
            raw = sum(p * _overlap(AGE_BUCKET_RANGES[b], span) for b, p in features.age_bucket.distribution.items() if b in AGE_BUCKET_RANGES)
            like *= _temper(0.4 + raw, features.age_bucket.confidence)
        if persona.id == "persona_010" and features.gift_signal.value:
            like *= _temper(1 + cfg["gift_likelihood_scale"] * float(features.gift_signal.value), features.gift_signal.confidence)
        if persona.id == "persona_007" and features.delivery_speed_proxy.value == "same_or_next_day_likely":
            like *= _temper(cfg["fast_delivery_boost"], features.delivery_speed_proxy.confidence)
        if use_household:
            for link in household_cfg["persona_links"]:
                if link["persona"] != persona.id:
                    continue
                feat = getattr(features, link["feature"])
                if feat.value is not None:
                    raw = max(0.2, 1 + link["strength"] * (float(feat.value) - link["baseline"]))
                    like *= _temper(raw, feat.confidence)
        scores[persona.id] = prior * like
    total = sum(scores.values())
    posterior = {pid: round(v / total, 3) for pid, v in sorted(scores.items(), key=lambda kv: -kv[1])}
    notes.append(f"prior from {pub.name}'s audience; name {'used' if use_name else 'ignored'} "
                 f"(gender conf {features.p_female.confidence:.2f}, age conf {features.age_bucket.confidence:.2f})")
    return posterior, notes


def _campaign_tags(plan: CampaignPlan) -> set[str]:
    """Host-vocabulary tags plus persona-vocabulary tags (e.g. kids_products, home_goods)."""
    return {t.tag for t in plan.profile.relevant_tags} | set(plan.profile.persona_affinity_tags)


def _context_multiplier(features: ShopperFeatures, plan: CampaignPlan, use_household: bool = True) -> tuple[float, list[str]]:
    cfg = _cfg()
    tags, mult, why = _campaign_tags(plan), 1.0, []
    if use_household:
        for rule in catalog.config("household")["tag_boosts"]:
            feat = getattr(features, rule["feature"])
            if feat.value is not None and float(feat.value) >= rule["threshold"] and tags & set(rule["tags"]):
                m = _temper(rule["multiplier"], feat.confidence)
                mult *= m
                why.append(f"{rule['feature']}={float(feat.value):.2f} x{m:.2f}")
    for rule in cfg["context_boosts"]:
        feat = getattr(features, rule["feature"])
        if feat.value is True and tags & set(rule["tags"]):
            m = _temper(rule["multiplier"], feat.confidence)
            mult *= m
            why.append(f"{rule['feature']} x{m:.2f}")
    from disco_planner.serving.calendar import load_events

    moments = features.active_moments.distribution or {}
    for event in load_events():
        intensity = moments.get(event["name"], 0)
        if intensity and tags & set(event.get("tags", [])):
            m = 1 + cfg["moment_boost_max"] * intensity
            mult *= m
            why.append(f"{event['name']} x{m:.2f}")
    return mult, why


def _render(template: str, fallback: str, features: ShopperFeatures) -> tuple[str, bool]:
    name = features.display_name.value
    if name and "{first_name}" in template:
        return template.replace("{first_name}", str(name)), True
    return fallback, False


def host_prior(publisher_id: str) -> dict[str, float]:
    """The host's AVERAGE shopper: persona mix from the audience alone (no shopper signals)."""
    pub = catalog.publisher_by_id()[publisher_id]
    raw = {p.id: host_fit(p, pub) for p in catalog.personas()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def final_bid(plan: CampaignPlan, publisher_id: str, match: float, prior_match: float,
              phase: str, pacing_ratio: float) -> tuple[float, str]:
    """Bid strategy at serve time (config.bid_strategy). pacing_ratio = share of budget spent /
    share of flight elapsed (1.0 = on plan)."""
    cfg = plan.config
    pricing, strategy = cfg.pricing, cfg.bid_strategy
    adj = catalog.config("pricing")["bid_strategy"]["adjustments"]
    clamp = lambda x, lo, hi: max(lo, min(hi, x))
    pace = clamp(1 / max(pacing_ratio, 1e-6), adj["pacing"]["min"], adj["pacing"]["max"])

    if pricing.model == "CPM" and pricing.cpm_bid:
        bid = pricing.cpm_bid * pace
        return round(bid, 2), f"CPM ${pricing.cpm_bid} x pacing {pace:.2f}"
    alloc = next(a for a in cfg.budget.allocations if a.publisher_id == publisher_id)
    host = next((h for h in strategy.host_bids if h.publisher_id == publisher_id), None) if strategy else None
    start = host.start_bid if host else (alloc.start_bid_usd or alloc.cpc_bid_usd)
    ceiling = host.max_bid if host else alloc.cpc_bid_usd
    floor = host.floor if host else catalog.config("pricing")["economics"]["cpc_floor"]

    conv = 1.0
    if phase == "enhanced_cpc":
        conv = clamp(match / prior_match if prior_match else 1.0,
                     adj["conversion_likelihood"]["min"], adj["conversion_likelihood"]["max"])
    bid = clamp(start * conv * pace, floor, ceiling)
    capped = " (capped at break-even)" if start * conv * pace > ceiling else ""
    return round(bid, 2), (f"{phase}: start ${start:.2f} x conversion likelihood {conv:.2f} x pacing {pace:.2f} "
                           f"= ${bid:.2f}{capped}; ceiling ${ceiling:.2f}")


def serve(
    signals: ShopperSignals, now_utc: datetime, plans: list[CampaignPlan],
    use_name: bool = True, use_household: bool = True,
    phase: str = "learning", pacing_ratio: float = 1.0,
) -> ServeDecision:
    cfg = _cfg()
    features = build_features(signals, now_utc)
    posterior, reasons = persona_posterior(features, signals.publisher_id, use_name, use_household)
    uncertain = max(posterior.values()) < cfg["posterior"]["uncertain_max_prob"]
    if uncertain:
        reasons.append("persona posterior is flat (low confidence): still picking the most likely persona; "
                       "in production this is where the bandit explores")

    priors = catalog.config("pricing")["priors"]
    prior = host_prior(signals.publisher_id)
    offers = []
    for plan in plans:
        cfg_ = plan.config
        if cfg_.status != "draft" or not cfg_.budget:
            continue
        alloc = next((a for a in cfg_.budget.allocations if a.publisher_id == signals.publisher_id), None)
        if not alloc or not cfg_.creatives:
            continue  # this campaign doesn't run on this host
        score = next(s for s in plan.publishers if s.publisher_id == signals.publisher_id)
        top = max(s.score for s in plan.publishers)
        fit = relative_fit(score.score, top)

        # Variant choice with a margin: start from the host-default variant (best prior fit) and
        # switch only if the shopper's signals make another persona clearly more likely. Without
        # this, near-tied personas flip on tiny name-based nudges (the ablation showed 42%).
        by_id = catalog.persona_by_id()
        pub = catalog.publisher_by_id()[signals.publisher_id]
        default = max(cfg_.creatives, key=lambda c: host_fit(by_id[c.persona_id], pub))
        best = max(cfg_.creatives, key=lambda c: posterior.get(c.persona_id, 0))
        margin = cfg["posterior"]["switch_margin"]
        if posterior.get(best.persona_id, 0) > (1 + margin) * posterior.get(default.persona_id, 0):
            creative = best
        else:
            creative = default
        match = sum(posterior.get(c.persona_id, 0) for c in cfg_.creatives)
        rk = cfg["ranking"]
        ctx, ctx_why = _context_multiplier(features, plan, use_household)
        ctr = priors["ctr"]["mid"] * fit * (rk["persona_match_floor"] + rk["persona_match_span"] * min(1.0, match * 2)) * ctx
        pricing = cfg_.pricing
        prior_match = sum(prior.get(c.persona_id, 0) for c in cfg_.creatives)
        bid, breakdown = final_bid(plan, signals.publisher_id, match, prior_match, phase, pacing_ratio)
        ecpm = bid if pricing.model == "CPM" else bid * ctr * 1000
        headline, used = _render(creative.headline_template, creative.headline_no_name, features)
        offers.append(Offer(
            campaign=plan.profile.product or plan.brief[:40], publisher_id=signals.publisher_id,
            persona_id=creative.persona_id, headline=headline, body=creative.body, cta=creative.cta,
            pricing_model=pricing.model, bid=bid, bid_breakdown=breakdown, expected_ctr=round(ctr, 4), ecpm=round(ecpm, 2),
            reasons=[f"host fit x{fit:.2f}", f"persona match {match:.2f} -> variant for {by_id[creative.persona_id].name}",
                     *ctx_why, f"bid {breakdown}", f"{'name inserted' if used else 'no-name headline'}"],
        ))
    offers.sort(key=lambda o: -o.ecpm)
    offers = offers[: cfg["ranking"]["max_offers"]]
    if not offers:
        reasons.append("no approved campaign runs on this host")
    top_offer = offers[0] if offers else None
    return ServeDecision(
        persona_posterior={catalog.persona_by_id()[k].name: v for k, v in posterior.items()},
        uncertain=uncertain, offers=offers,
        headline=top_offer.headline if top_offer else None, body=top_offer.body if top_offer else None,
        cta=top_offer.cta if top_offer else None, name_used="name inserted" in (top_offer.reasons if top_offer else []),
        reasons=reasons + ([f"ranked {len(offers)} offers by eCPM = final bid x expected CTR x 1000 (bid phase: {phase})"] if offers else []),
    )
