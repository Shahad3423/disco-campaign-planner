"""Stage orchestration: brief -> CampaignPlan. Each stage is timed and logged so the UI
and evals can show what happened and what it cost."""
from __future__ import annotations

import time
from datetime import date, datetime

from pydantic import BaseModel, Field

from disco_planner.data import catalog
from disco_planner.domain.models import (
    AdvertiserInputs, AdvertiserProfile, CampaignConfig, CreativeAssignment, CreativeReview,
    CreativeVariant, PersonaMatch, PublisherScore,
)
from disco_planner.llm.provider import Provider
from disco_planner.planning import budget, creative, intake, personas, publisher_scoring
from disco_planner.serving.calendar import active_moments


class CampaignPlan(BaseModel):
    brief: str
    profile: AdvertiserProfile
    publishers: list[PublisherScore]
    personas: list[PersonaMatch]
    creatives: list[CreativeVariant] = Field(default_factory=list)
    reviews: list[CreativeReview] = Field(default_factory=list)
    config: CampaignConfig
    timings_s: dict[str, float]
    creative_log: dict = Field(default_factory=dict)


def _primary_category(profile: AdvertiserProfile, scores: list[PublisherScore]) -> str | None:
    categories = {p.category for p in catalog.publishers()}
    tagged = [t for t in sorted(profile.relevant_tags, key=lambda t: -t.weight) if t.tag in categories]
    if tagged:
        return tagged[0].tag
    top = next((s for s in scores if s.recommended), None)
    return catalog.publisher_by_id()[top.publisher_id].category if top else None


def run(
    brief: str, provider: Provider, today: date, inputs: AdvertiserInputs | None = None,
    with_creatives: bool = True,
) -> CampaignPlan:
    inputs = inputs or AdvertiserInputs()
    t: dict[str, float] = {}

    start = time.perf_counter()
    profile = intake.parse_brief(brief, provider, today)
    t["intake"] = time.perf_counter() - start

    start = time.perf_counter()
    scores = publisher_scoring.rank_publishers(profile, today)
    persona_matches = personas.select_personas(profile, scores)
    t["matching"] = time.perf_counter() - start

    moments = [m for m in active_moments(datetime(today.year, today.month, today.day, 12)) if m.intensity > 0]
    moment_names = [m.name for m in moments]
    gifting_moment = next((m.name for m in moments if m.gifting), None)

    eligible = profile.gate in ("proceed", "clarify")
    selected = [m for m in persona_matches if m.selected]
    variants, reviews, log = [], [], {}
    if eligible and with_creatives and selected:
        start = time.perf_counter()
        variants, reviews, log = creative.generate_creatives(profile, selected, moment_names, provider)
        t["creative"] = time.perf_counter() - start

    eco = pricing = plan = None
    if eligible and any(s.recommended for s in scores):
        eco = budget.economics(profile, inputs, _primary_category(profile, scores))
        pricing = budget.pricing_plan(eco, inputs)
        plan = budget.budget_plan(scores, eco, pricing, inputs, today, gifting_moment)

    status = {"proceed": "draft", "clarify": "needs_clarification"}.get(profile.gate, "not_eligible")
    by_persona = {m.persona_id: m for m in persona_matches}
    allowed_hosts = {a.publisher_id for a in plan.allocations} if plan else set()
    config = CampaignConfig(
        status=status, planning_date=today.isoformat(), advertiser_summary=profile.business_summary or brief,
        confidence=profile.confidence, objective=inputs.objective,
        economics=eco, pricing=pricing, budget=plan,
        targeting={
            "publishers_include": sorted(allowed_hosts),
            "publishers_exclude": [{"id": s.publisher_id, "reason": s.reason} for s in scores if not s.recommended],
            "personas": [m.persona_id for m in selected],
            "geo": "US (no advertiser geo restriction stated)",
            "serve_time_signals": {
                "platform": "publisher_id -> cart prior (category, AOV)",
                "zip": "billing zip preferred; geo features down-weighted if shipping/unknown",
                "first_name": "personalisation only + weak cohort prior; never in hard targeting",
            },
            "seasonal_moments": moment_names,
        },
        creatives=[
            CreativeAssignment(
                variant_id=f"v{i + 1}", persona_id=v.persona_id,
                publisher_ids=[p for p in by_persona[v.persona_id].best_publishers if p in allowed_hosts],
                headline_template=v.headline, headline_no_name=v.headline_no_name, body=v.body, cta=v.cta,
            )
            for i, v in enumerate(variants) if v.persona_id in by_persona
        ],
        frequency_cap=catalog.config("pricing")["network"]["frequency_cap"],
        guardrails={
            "claims_allowed": profile.stated_claims,
            "claims_blocked_pending_substantiation": profile.claims_needing_substantiation,
            "competitor_hosts_need_approval": [s.publisher_id for s in scores if s.recommended and s.flags],
            "stop_loss": catalog.config("pricing")["budget"]["stop_loss"],
            "creative_rules": "no inferred-demographic references; {first_name} falls back to headline_no_name",
        },
        measurement={
            "primary_kpi": "CPA vs target" if inputs.objective == "acquisition" else "reach and CTR",
            "attribution": "7-day click-through billed; view-through reported, never billed",
            "holdout": "10% of eligible impressions withheld to measure incremental lift",
            "review_after_days": 14,
        },
        open_questions=profile.clarifying_questions,
    )
    return CampaignPlan(
        brief=brief, profile=profile, publishers=scores, personas=persona_matches,
        creatives=variants, reviews=reviews, config=config,
        timings_s={k: round(v, 2) for k, v in t.items()}, creative_log=log,
    )
