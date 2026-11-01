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
        default_factory=list, description="Leave empty: filled by code from the claims policy"
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
    value: float | None = Field(default=None, ge=0, le=1, description="None = not stated, left out of the score")
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
    variant: int = Field(default=1, ge=1, le=5, description="1..n within the persona: different angles to A/B/C test")
    headline: str = Field(description="<= 60 chars. May contain {first_name} once.")
    body: str = Field(description="<= 140 chars, fits a thank-you-page offer card")
    cta: str
    headline_no_name: str = Field(description="Fallback when the shopper's name is unusable")
    angle: str = Field(description="Which persona preference this variant leans on")
    claims_used: list[str] = Field(default_factory=list)


class CreativeReview(BaseModel):
    persona_id: str
    variant: int = 1
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

Objective = Literal["conversions", "clicks", "impressions", "reach"]
Bidding = Literal["maximize", "target_cpa", "target_roas"]
Aggressiveness = Literal["conservative", "balanced", "aggressive"]
PricingModel = Literal["CPC", "CPM", "tCPA"]
OBJECTIVE_UNIT = {"conversions": "conversions", "clicks": "clicks", "impressions": "impressions", "reach": "unique shoppers"}


class AdvertiserInputs(BaseModel):
    """What the advertiser sets. The budget is THEIRS to choose: we never suggest one (D23).
    Without a budget we still rank hosts, pick personas and write creatives, but don't allocate."""

    monthly_budget_usd: float | None = Field(default=None, gt=0, description="hard spending cap for the flight")
    flight_days: int | None = None
    objective: Objective = "conversions"
    goal_target: int | None = Field(default=None, gt=0, description="optional: number of objective units to reach")
    when_goal_met: Literal["stop", "continue"] = "stop"
    bidding: Bidding = "maximize"
    target_cpa_usd: float | None = Field(default=None, gt=0)
    target_roas: float | None = Field(default=None, gt=0, description="revenue / ad spend, e.g. 3.0")
    max_cpc_usd: float | None = Field(default=None, gt=0, description="optional hard bid cap per click (yours)")
    aggressiveness: Aggressiveness = "balanced"
    max_share_per_host: float | None = Field(default=None, gt=0, le=1, description="None/1.0 = off (default); e.g. 0.5 spreads risk")
    price_usd: float | None = Field(default=None, description="only needed for target ROAS")
    has_conversion_tracking: bool = False


class Range(BaseModel):
    low: float
    mid: float
    high: float


class Economics(BaseModel):
    """Only what the advertiser told us. Margins and break-even are the advertiser's business (D24)."""

    price_usd: float
    price_source: Literal["stated", "form", "tier_default"]
    expected_orders: float
    target_cpa: float | None = None
    target_source: Literal["target_cpa", "target_roas", "none"] = "none"
    assumptions: list[str]


class PricingPlan(BaseModel):
    model: PricingModel
    bidding: Bidding
    aggressiveness: Aggressiveness
    rationale: str
    max_cpc: float | None = Field(default=None, description="the advertiser's own cap, if any")
    cpc_range: Range | None = Field(default=None, description="market price to win across the recommended hosts")
    cpm_bid: float | None = None
    target_cpa: float | None = None
    tcpa_unlock: str


class Allocation(BaseModel):
    publisher_id: str
    publisher_name: str
    share: float
    budget_usd: float
    cpc_bid_usd: float = Field(description="planned bid on this host (the price that wins the planned share)")
    start_bid_usd: float | None = None
    win_share: float = Field(description="share of this host's page views the planned bid wins")
    avg_price_paid: float = Field(description="average second price paid per click")
    market_price: Range = Field(description="price to win a page view here: 25th / 50th / 75th percentile")
    audience_weight: float = Field(default=1.0, description="share of units counted as in-audience")
    expected_clicks: Range
    expected_conversions: Range
    expected_units: Range = Field(description="forecast in the objective's unit")
    capacity_usd: float = Field(description="spend if we won every page view we'd bid for here")
    expected_impressions: int = 0
    expected_reach: int = 0
    requires_host_approval: bool
    rationale: str


class BudgetPlan(BaseModel):
    monthly_budget_usd: float = Field(description="the advertiser's cap")
    planned_spend_usd: float = Field(description="<= budget; less when stopping at a reached goal")
    objective: Objective
    unit: str
    goal_target: int | None = None
    when_goal_met: Literal["stop", "continue"] = "stop"
    aggressiveness: Aggressiveness = "balanced"
    max_share_per_host: float = 1.0
    forecast: Range = Field(description="objective units the planned spend buys")
    forecast_without_share_cap: float | None = Field(default=None, description="mid forecast if the share cap were off")
    goal_status: Literal["no_goal", "reachable", "money_limited", "inventory_limited"]
    binding_constraint: Literal["budget", "inventory", "goal"]
    spend_to_reach_goal_usd: float | None = None
    max_deliverable: float = Field(description="units available at this aggressiveness if budget were unlimited")
    spend_at_full_inventory_usd: float
    replan_options: list[str] = Field(default_factory=list)
    expected_impressions: int = 0
    expected_reach: int = 0
    daily_cap_usd: float
    flight_start: str
    flight_end: str
    pacing: str
    allocations: list[Allocation]
    warnings: list[str] = Field(default_factory=list)


class HostBid(BaseModel):
    publisher_id: str
    start_bid: float
    max_bid: float = Field(description="hard cap; never exceeded")
    floor: float


class BidAdjustment(BaseModel):
    signal: str
    min_multiplier: float
    max_multiplier: float
    reason: str


class BidStrategy(BaseModel):
    """How the price is set per impression (glossary: 'bid higher when the user looks like a
    high-converter'). Phased, because adjusting bids before we can measure anything just adds noise."""

    current_phase: Literal["learning", "maximize", "enhanced_cpc", "target_cpa", "fixed_cpm"]
    phases: list[dict]
    host_bids: list[HostBid]
    adjustments: list[BidAdjustment]
    rationale: str


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
    bid_strategy: BidStrategy | None = None
    targeting: dict
    creatives: list[CreativeAssignment]
    frequency_cap: dict
    guardrails: dict
    measurement: dict
    open_questions: list[str]
