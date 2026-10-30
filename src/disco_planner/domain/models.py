"""Typed contracts between pipeline stages.

Every stage is `input model -> output model`, so stages can be tested, swapped,
or moved behind a service boundary without touching callers (DECISIONS D11).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- catalog


class Audience(BaseModel):
    age_skew: str
    gender_split: dict[str, float]
    top_geos: list[str]
    income_tier: str


class Publisher(BaseModel):
    id: str
    name: str
    category: str
    subcategories: list[str]
    monthly_impressions: int
    avg_order_value_usd: float
    audience: Audience
    notes: str

    @property
    def tags(self) -> set[str]:
        return {self.category, *self.subcategories}


class Persona(BaseModel):
    id: str
    name: str
    age_range: str
    gender_skew: str
    description: str
    category_affinities: list[str]
    price_sensitivity: str
    messaging_preferences: list[str]
    disinterested_in: list[str]
    typical_aov_usd: float


# ---------------------------------------------------------------- stage 1: intake

PriceTier = Literal["budget", "mid", "premium", "luxury", "unknown"]
PurchaseModel = Literal["one_time", "repeat_consumable", "subscription", "gift", "unknown"]
Gate = Literal["proceed", "clarify", "reject_low_signal", "off_network"]


class TagRelevance(BaseModel):
    tag: str = Field(description="A tag from the closed catalog vocabulary")
    weight: float = Field(ge=0, le=1, description="1 = core fit, 0.5 = adjacent, 0.25 = weak")
    why: str


class NoteSignal(BaseModel):
    """LLM's read of a publisher's free-text `notes` against this advertiser."""

    publisher_id: str
    alignment: Literal[-1, 0, 1]
    why: str


class PersonaConflict(BaseModel):
    persona_id: str
    conflict: str = Field(description="Which disinterested_in / preference is violated")
    evidence: str = Field(description="The part of the brief that causes it")


class AdvertiserProfile(BaseModel):
    """What the LLM understood from the advertiser's text. Facts only: anything
    not stated is `unknown` / None and listed in `missing_info`."""

    business_summary: str
    product: str
    is_consumer_product: bool
    price_point_usd: float | None = None
    price_tier: PriceTier = "unknown"
    purchase_model: PurchaseModel = "unknown"
    target_gender: Literal["female", "male", "any", "unknown"] = "unknown"
    target_age_range: str | None = None
    stated_claims: list[str] = Field(default_factory=list, description="Claims the advertiser actually made")
    claims_needing_substantiation: list[str] = Field(
        default_factory=list, description="Regulated claims (health, approval, guarantees) we won't use without proof"
    )
    values: list[str] = Field(default_factory=list)
    fulfillment_notes: str | None = None
    relevant_tags: list[TagRelevance] = Field(default_factory=list)
    competing_tags: list[str] = Field(default_factory=list, description="Catalog tags of publishers that sell the same thing")
    persona_affinity_tags: list[str] = Field(default_factory=list)
    persona_conflicts: list[PersonaConflict] = Field(default_factory=list)
    note_signals: list[NoteSignal] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    gate: Gate
    gate_reason: str
    missing_info: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    # Filled by code (self-consistency), not by the model:
    tag_stability: dict[str, float] = Field(default_factory=dict, description="share of samples that chose each tag")
    sample_agreement: float | None = None
    competing_tag_share: dict[str, float] = Field(default_factory=dict, description="share of samples flagging each competitor tag")


# ---------------------------------------------------------------- stage 2: matching


class ScoreComponent(BaseModel):
    name: str
    value: float = Field(ge=0, le=1)
    weight: float
    detail: str


class PublisherScore(BaseModel):
    publisher_id: str
    publisher_name: str
    score: float
    components: list[ScoreComponent]
    flags: list[str] = Field(default_factory=list)
    recommended: bool
    reason: str


class PersonaMatch(BaseModel):
    persona_id: str
    persona_name: str
    score: float
    selected: bool
    reasons: list[str]
    best_publishers: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- stage 3: creative


class CreativeVariant(BaseModel):
    persona_id: str
    headline: str = Field(description="<= 60 chars. May contain {first_name} once.")
    body: str = Field(description="<= 140 chars, fits a thank-you-page offer card")
    cta: str
    headline_no_name: str = Field(description="Fallback when the shopper's name is unusable")
    angle: str = Field(description="Which persona preference this variant leans on")
    claims_used: list[str] = Field(default_factory=list)


class CreativeReview(BaseModel):
    persona_id: str
    persona_fit: int = Field(ge=1, le=5)
    claims_ok: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    distinct_from_others: bool
    issues: list[str] = Field(default_factory=list)
    passed: bool


# ---------------------------------------------------------------- stage 4: campaign config
# Shape rationale (README / DECISIONS D2, D6): every field is something a serving system,
# a billing system, or a human approver needs; assumptions are first-class fields so the
# advertiser can see and override them.

Objective = Literal["acquisition", "awareness"]
PricingModel = Literal["CPC", "CPM", "tCPA"]


class AdvertiserInputs(BaseModel):
    """Optional form fields. Anything left empty falls back to a labelled default."""

    monthly_budget_usd: float | None = None
    price_usd: float | None = None
    gross_margin: float | None = Field(default=None, ge=0, le=1)
    objective: Objective = "acquisition"
    flight_days: int | None = None
    has_conversion_tracking: bool = False


class Range(BaseModel):
    low: float
    mid: float
    high: float


class Economics(BaseModel):
    price_usd: float
    price_source: Literal["stated", "form", "tier_default"]
    gross_margin: float
    margin_source: Literal["form", "category_default"]
    expected_orders: float
    max_affordable_cac: float
    target_cpa: float
    assumptions: list[str]


class PricingPlan(BaseModel):
    model: PricingModel
    rationale: str
    cpc_bid: Range | None = None
    cpm_bid: float | None = None
    target_cpa: float
    tcpa_unlock: str


class Allocation(BaseModel):
    publisher_id: str
    publisher_name: str
    share: float
    budget_usd: float
    cpc_bid_usd: float = Field(description="Max (break-even) CPC for this host")
    start_bid_usd: float | None = Field(default=None, description="Where to start bidding; raise only if under-delivering")
    expected_clicks: Range
    expected_conversions: Range
    capacity_usd: float
    requires_host_approval: bool
    rationale: str


class BudgetPlan(BaseModel):
    monthly_budget_usd: float
    budget_source: Literal["form", "recommended_learning_budget"]
    daily_cap_usd: float
    flight_start: str
    flight_end: str
    pacing: str
    learning_budget_needed_usd: float
    exploration_reserve_usd: float
    exploration_candidates: list[str]
    allocations: list[Allocation]
    warnings: list[str] = Field(default_factory=list)


class CreativeAssignment(BaseModel):
    variant_id: str
    persona_id: str
    publisher_ids: list[str]
    headline_template: str
    headline_no_name: str
    body: str
    cta: str


class CampaignConfig(BaseModel):
    schema_version: str = "1.0"
    status: Literal["draft", "needs_clarification", "not_eligible"]
    planning_date: str
    advertiser_summary: str
    confidence: float
    objective: Objective
    economics: Economics | None
    pricing: PricingPlan | None
    budget: BudgetPlan | None
    targeting: dict
    creatives: list[CreativeAssignment]
    frequency_cap: dict
    guardrails: dict
    measurement: dict
    open_questions: list[str]
