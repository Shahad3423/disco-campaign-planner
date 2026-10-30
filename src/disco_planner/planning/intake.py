"""Stage 1: advertiser text -> AdvertiserProfile, then sanity checks in code."""
from __future__ import annotations

from datetime import date

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserProfile
from disco_planner.llm import prompts
from disco_planner.llm.provider import Provider, generate

MIN_WORDS = 3


def _catalog_context() -> dict[str, str]:
    pubs = "\n".join(
        f"- {p.id} {p.name}: {p.category} / {', '.join(p.subcategories)}. Notes: {p.notes}"
        for p in catalog.publishers()
    )
    pers = "\n".join(
        f"- {p.id} {p.name}: disinterested_in={p.disinterested_in}; prefers={p.messaging_preferences}; "
        f"price_sensitivity={p.price_sensitivity}"
        for p in catalog.personas()
    )
    return {
        "publisher_tags": ", ".join(catalog.publisher_tag_vocabulary()),
        "persona_tags": ", ".join(catalog.persona_tag_vocabulary()),
        "publishers": pubs,
        "personas": pers,
    }


def _enforce_vocabulary(profile: AdvertiserProfile) -> AdvertiserProfile:
    """The model is told to use closed vocabularies; code makes sure of it."""
    pub_vocab = set(catalog.publisher_tag_vocabulary())
    persona_vocab = set(catalog.persona_tag_vocabulary())
    pub_ids = {p.id for p in catalog.publishers()}
    persona_ids = {p.id for p in catalog.personas()}
    profile.relevant_tags = [t for t in profile.relevant_tags if t.tag in pub_vocab]
    profile.competing_tags = [t for t in profile.competing_tags if t in pub_vocab]
    profile.persona_affinity_tags = [t for t in profile.persona_affinity_tags if t in persona_vocab]
    profile.note_signals = [s for s in profile.note_signals if s.publisher_id in pub_ids]
    profile.persona_conflicts = [c for c in profile.persona_conflicts if c.persona_id in persona_ids]
    return profile


def parse_brief(brief: str, provider: Provider, today: date) -> AdvertiserProfile:
    brief = brief.strip()
    if len(brief.split()) < MIN_WORDS:
        # Not worth an LLM call; answer instantly and cheaply.
        return AdvertiserProfile(
            business_summary="", product="", is_consumer_product=False, confidence=0.0,
            gate="reject_low_signal", gate_reason="The description is too short to tell what you sell.",
            missing_info=["product", "audience", "price"],
            clarifying_questions=["What do you sell, in one sentence?", "Who usually buys it?", "Roughly what does it cost?"],
        )
    system, user = prompts.render("intake", brief=brief, today=today.isoformat(), **_catalog_context())
    profile = _enforce_vocabulary(generate(provider, system, user, AdvertiserProfile, temperature=0.0))

    # Code-level guardrails on the model's gate decision.
    if not profile.is_consumer_product and profile.gate == "proceed":
        profile.gate, profile.gate_reason = "off_network", "Not a consumer product a checkout shopper can buy."
    if profile.gate == "proceed" and not profile.relevant_tags:
        profile.gate, profile.gate_reason = "clarify", "No host category clearly fits; need more detail."
    return profile
