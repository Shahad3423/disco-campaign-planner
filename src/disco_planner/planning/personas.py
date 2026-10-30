"""Persona selection: which shopper types is this advertiser plausibly for, and where
do those shoppers show up among the recommended hosts?

Hard exclusions only come from conflicts with what the advertiser *stated*
(e.g. "ships in 6 weeks" vs a persona that dislikes delayed fulfillment).
Everything else is a soft score (DECISIONS D5, rule 3).
"""
from __future__ import annotations

import re

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserProfile, Persona, PersonaMatch, Publisher, PublisherScore


def _range(text: str | None) -> tuple[int, int] | None:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    return (nums[0], nums[1]) if len(nums) >= 2 else None


def _overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> float:
    """Share of range `a` covered by `b`; 0.6 (neutral) when either is unknown."""
    if not a or not b:
        return 0.6
    return max(0, min(a[1], b[1]) - max(a[0], b[0])) / max(1, a[1] - a[0])


def affinity(profile: AdvertiserProfile, persona: Persona) -> tuple[float, list[str]]:
    hits = sorted(set(profile.persona_affinity_tags) & set(persona.category_affinities))
    if not hits:
        return 0.0, []
    return min(1.0, 0.6 + 0.2 * (len(hits) - 1)), hits


def price_compat(profile: AdvertiserProfile, persona: Persona) -> float:
    table = catalog.config("scoring")["personas"]["price_compat"]
    return table.get(profile.price_tier, table["unknown"]).get(persona.price_sensitivity, 0.7)


def audience_compat(profile: AdvertiserProfile, persona: Persona) -> float:
    gender = 1.0
    if profile.target_gender in ("female", "male"):
        skew = persona.gender_skew
        if skew == "balanced":
            gender = 0.8
        elif skew.startswith(profile.target_gender):
            gender = 1.0
        else:
            gender = 0.3
    age = _overlap(_range(profile.target_age_range), _range(persona.age_range))
    return 0.5 * gender + 0.5 * max(age, 0.3)


def host_fit(persona: Persona, pub: Publisher) -> float:
    """How likely this persona is among this host's shoppers: demographics + category."""
    age = _overlap(_range(persona.age_range), _range(pub.audience.age_skew))
    female = pub.audience.gender_split.get("female", 0.5)
    gender = {"female": female, "female-leaning": 0.5 + female / 2, "balanced": 1 - abs(female - 0.5)}.get(
        persona.gender_skew, 0.5
    )
    category = 1.0 if set(persona.category_affinities) & (pub.tags | {pub.category}) else 0.4
    return round((age + gender + category) / 3, 3)


def persona_weights(profile: AdvertiserProfile) -> dict[str, float]:
    """Plausibility of each persona for this advertiser, before looking at hosts.
    Used by the publisher scorer for the persona-mediated (two-hop) term."""
    conflicts = {c.persona_id for c in profile.persona_conflicts}
    weights = {}
    for persona in catalog.personas():
        aff, _ = affinity(profile, persona)
        score = 0.0 if persona.id in conflicts else aff * price_compat(profile, persona) * audience_compat(profile, persona)
        if score > 0:
            weights[persona.id] = score
    return weights


def select_personas(profile: AdvertiserProfile, publisher_scores: list[PublisherScore]) -> list[PersonaMatch]:
    cfg = catalog.config("scoring")["personas"]
    conflicts = {c.persona_id: c for c in profile.persona_conflicts}
    recommended = [catalog.publisher_by_id()[s.publisher_id] for s in publisher_scores if s.recommended]

    matches = []
    for persona in catalog.personas():
        aff, hits = affinity(profile, persona)
        price = price_compat(profile, persona)
        aud = audience_compat(profile, persona)
        score = aff * price * aud
        reasons = [
            f"category affinity {aff:.2f}" + (f" ({', '.join(hits)})" if hits else " (no shared interests)"),
            f"price fit {price:.2f} ({profile.price_tier} product vs {persona.price_sensitivity} price sensitivity)",
            f"audience fit {aud:.2f}",
        ]
        if persona.id in conflicts:
            c = conflicts[persona.id]
            score = 0.0
            reasons.insert(0, f"EXCLUDED: conflicts with '{c.conflict}' because the brief says: {c.evidence}")
        hosts = sorted(recommended, key=lambda p: host_fit(persona, p), reverse=True)
        matches.append(PersonaMatch(
            persona_id=persona.id, persona_name=persona.name, score=round(score, 3), selected=False,
            reasons=reasons, best_publishers=[p.id for p in hosts[:3]],
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    if profile.gate in ("off_network", "reject_low_signal"):
        return matches
    for m in matches[: cfg["max_selected"]]:
        if m.score >= cfg["min_score"]:
            m.selected = True

    # Reach the minimum number of variants (the brief asks for 3-5) without pretending: fill
    # from non-conflicting personas most present on the recommended hosts, labelled exploratory.
    def presence(m: PersonaMatch) -> float:
        p = catalog.persona_by_id()[m.persona_id]
        return sum(host_fit(p, h) for h in recommended) / max(1, len(recommended))

    fill = sorted((m for m in matches if not m.selected and m.persona_id not in conflicts), key=lambda m: (-m.score, -presence(m)))
    for m in fill[: max(0, cfg["min_selected"] - sum(m.selected for m in matches))]:
        m.selected = True
        m.reasons.insert(0, f"EXPLORATORY: weak fit for the product, but common on the recommended hosts "
                            f"(presence {presence(m):.2f}); included to reach {cfg['min_selected']} variants")
    return matches
