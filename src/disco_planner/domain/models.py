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
