"""Stage 1: advertiser text -> AdvertiserProfile, then sanity checks in code."""
from __future__ import annotations

from collections import Counter
from datetime import date

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserProfile, NoteSignal, TagRelevance
from disco_planner.llm import prompts
from disco_planner.llm.provider import Provider, generate
from disco_planner.planning import claims

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
    # A competitor sells the same *product*: a subcategory like "bedding", never a whole
    # category ("apparel") or an attribute ("women"), which would flag half the catalog.
    too_broad = {p.category for p in catalog.publishers()} | set(catalog.config("scoring")["generic_tags"])
    profile.competing_tags = [t for t in profile.competing_tags if t in pub_vocab and t not in too_broad]
    profile.persona_affinity_tags = [t for t in profile.persona_affinity_tags if t in persona_vocab]
    profile.note_signals = [s for s in profile.note_signals if s.publisher_id in pub_ids]
    profile.persona_conflicts = [c for c in profile.persona_conflicts if c.persona_id in persona_ids]
    return profile


GATE_CAUTION = ["off_network", "reject_low_signal", "clarify", "proceed"]  # tie-break: most cautious


def _majority(items_per_sample: list[set], n: int) -> set:
    counts = Counter(x for items in items_per_sample for x in items)
    return {x for x, c in counts.items() if c * 2 > n}


def merge_samples(samples: list[AdvertiserProfile]) -> AdvertiserProfile:
    """Self-consistency: keep what most samples agree on; average weights.
    Sample 0 (temperature 0) supplies the free-text fields."""
    n = len(samples)
    primary = samples[0].model_copy(deep=True)
    if n == 1:
        return primary

    tag_sets = [{t.tag for t in s.relevant_tags} for s in samples]
    kept = _majority(tag_sets, n)
    first_seen = {}
    for s in samples:
        for t in s.relevant_tags:
            first_seen.setdefault(t.tag, t)
    primary.relevant_tags = sorted(
        (
            TagRelevance(tag=tag, why=first_seen[tag].why,
                         weight=round(sum(t.weight for s in samples for t in s.relevant_tags if t.tag == tag) / n, 3))
            for tag in kept
        ),
        key=lambda t: -t.weight,
    )
    primary.tag_stability = {tag: round(sum(tag in ts for ts in tag_sets) / n, 2) for tag in first_seen}
    # Asymmetric loss: a missed competitor wastes budget on a host that will block us; a false
    # flag only adds "needs host approval". So competitors are flagged if ANY sample says so,
    # while persona conflicts (which remove a persona) need a majority.
    competitor_sets = [set(s.competing_tags) for s in samples]
    primary.competing_tags = sorted(set().union(*competitor_sets))
    primary.competing_tag_share = {
        tag: round(sum(tag in cs for cs in competitor_sets) / n, 2) for tag in primary.competing_tags
    }
    primary.persona_affinity_tags = sorted(_majority([set(s.persona_affinity_tags) for s in samples], n))
    conflict_ids = _majority([{c.persona_id for c in s.persona_conflicts} for s in samples], n)
    primary.persona_conflicts = [
        next(c for s in samples for c in s.persona_conflicts if c.persona_id == pid) for pid in sorted(conflict_ids)
    ]
    notes = {}
    for s in samples:
        for sig in s.note_signals:
            notes.setdefault(sig.publisher_id, []).append(sig)
    primary.note_signals = [
        NoteSignal(publisher_id=pid, why=sigs[0].why, alignment=round(sum(x.alignment for x in sigs) / len(sigs)))
        for pid, sigs in notes.items() if len(sigs) * 2 > n
    ]
    gates = Counter(s.gate for s in samples)
    top = max(gates.values())
    primary.gate = min((g for g, c in gates.items() if c == top), key=GATE_CAUTION.index)
    if primary.gate != samples[0].gate:
        primary.gate_reason = next(s.gate_reason for s in samples if s.gate == primary.gate)

    pairs = [(a, b) for idx, a in enumerate(tag_sets) for b in tag_sets[idx + 1:]]
    agreement = sum(len(a & b) / max(1, len(a | b)) for a, b in pairs) / len(pairs)
    primary.sample_agreement = round(agreement, 2)
    primary.confidence = round(sum(s.confidence for s in samples) / n * (0.5 + 0.5 * agreement), 2)
    return primary


def parse_brief(brief: str, provider: Provider, today: date, n_samples: int | None = None) -> AdvertiserProfile:
    brief = brief.strip()
    if len(brief.split()) < MIN_WORDS:
        # Not worth an LLM call; answer instantly and cheaply.
        return AdvertiserProfile(
            business_summary="", product="", is_consumer_product=False, confidence=0.0,
            gate="reject_low_signal", gate_reason="The description is too short to tell what you sell.",
            missing_info=["product", "audience", "price"],
            clarifying_questions=["What do you sell, in one sentence?", "Who usually buys it?", "Roughly what does it cost?"],
        )
    n = n_samples or catalog.config("scoring")["intake"]["self_consistency_samples"]
    system, user = prompts.render("intake", brief=brief, today=today.isoformat(), **_catalog_context())
    samples = []
    for k in range(n):
        # Sample 0 is greedy; the rest are diverse. The sample id goes in the prompt so each
        # sample gets its own cache entry (otherwise the cache would return sample 0 n times).
        temp, suffix = (0.0, "") if k == 0 else (0.8, f"\n\n(sample {k})")
        samples.append(_enforce_vocabulary(generate(provider, system, user + suffix, AdvertiserProfile, temperature=temp)))
    profile = merge_samples(samples)

    # Regulated claims leave the allowed list, and we ask for substantiation instead.
    regulated = [c for c in profile.stated_claims if claims.violations(c)]
    if regulated:
        profile.stated_claims = [c for c in profile.stated_claims if c not in regulated]
        profile.claims_needing_substantiation = regulated
        profile.clarifying_questions.append(
            "We can't use these claims without substantiation (study, certification, or approval document): "
            + "; ".join(regulated) + ". Can you share it?"
        )

    # Code-level guardrails on the model's gate decision.
    if not profile.is_consumer_product and profile.gate == "proceed":
        profile.gate, profile.gate_reason = "off_network", "Not a consumer product a checkout shopper can buy."
    if profile.gate == "proceed" and not profile.relevant_tags:
        profile.gate, profile.gate_reason = "clarify", "No host category clearly fits; need more detail."
    return profile
