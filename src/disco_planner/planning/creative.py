"""Stage 3: persona-tuned creatives = generate -> rule checks + LLM review -> one rewrite.

Two independent gates, because each catches what the other misses:
- rule checks (code): lengths, placeholder count, banned patterns. Cheap, exact.
- LLM review (separate call): persona fit, unsupported claims, distinctness. Semantic.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserProfile, CreativeReview, CreativeVariant, PersonaMatch
from disco_planner.llm import prompts
from disco_planner.llm.provider import Provider, generate
from disco_planner.planning import claims

PLACEHOLDER = "{first_name}"
NAME_LEN = 8  # a typical first name, used when counting headline length
DEMOGRAPHIC_PATTERNS = re.compile(
    r"\b(as a (mom|dad|woman|man|millennial|boomer)|ladies|guys|since you('re| are) in|near you|your (city|town))\b",
    re.IGNORECASE,
)


class CreativeSet(BaseModel):
    variants: list[CreativeVariant]


class ReviewSet(BaseModel):
    reviews: list[CreativeReview]


def rule_check(v: CreativeVariant) -> list[str]:
    issues = []
    headline_len = len(v.headline.replace(PLACEHOLDER, "x" * NAME_LEN))
    if headline_len > 60:
        issues.append(f"headline {headline_len} chars > 60")
    if len(v.headline_no_name) > 60:
        issues.append(f"headline_no_name {len(v.headline_no_name)} chars > 60")
    if len(v.body) > 140:
        issues.append(f"body {len(v.body)} chars > 140")
    if len(v.cta) > 20:
        issues.append(f"cta {len(v.cta)} chars > 20")
    if v.headline.count(PLACEHOLDER) > 1:
        issues.append("placeholder used more than once")
    for rule in claims.violations(f"{v.headline} {v.headline_no_name} {v.body} {v.cta}"):
        issues.append(f"regulated claim without substantiation ({rule})")
    if PLACEHOLDER in v.headline_no_name or PLACEHOLDER in v.body:
        issues.append("placeholder outside the headline")
    for field in (v.headline, v.headline_no_name, v.body):
        if DEMOGRAPHIC_PATTERNS.search(field):
            issues.append(f"references inferred demographics/location: '{DEMOGRAPHIC_PATTERNS.search(field).group(0)}'")
        if field.count("!") > 1 or re.search(r"\b[A-Z]{5,}\b", field):
            issues.append("shouting (multiple ! or ALL CAPS)")
    return issues


def _persona_block(matches: list[PersonaMatch]) -> str:
    by_id = catalog.persona_by_id()
    lines = []
    for m in matches:
        p = by_id[m.persona_id]
        lines.append(
            f"- {p.id} {p.name}: {p.description} Prefers: {p.messaging_preferences}. "
            f"Avoid: {p.disinterested_in}. Price sensitivity: {p.price_sensitivity}."
        )
    return "\n".join(lines)


def generate_creatives(
    profile: AdvertiserProfile, selected: list[PersonaMatch], moments: list[str], provider: Provider,
) -> tuple[list[CreativeVariant], list[CreativeReview], dict]:
    """Returns variants, their final reviews, and a small log of what happened (for evals/UI)."""
    personas = _persona_block(selected)
    context = dict(
        summary=profile.business_summary, product=profile.product,
        claims=json.dumps(profile.stated_claims), values=json.dumps(profile.values),
        fulfillment=profile.fulfillment_notes or "none stated",
        moments=", ".join(moments) or "none", personas=personas,
    )
    n_variants = catalog.config("scoring")["personas"]["variants_per_persona"]
    key = lambda x: (x.persona_id, x.variant)
    log = {"rounds": 0, "rewritten": []}
    feedback = ""
    variants: list[CreativeVariant] = []
    reviews: list[CreativeReview] = []

    for round_ in range(2):  # generate, then at most one rewrite of failures
        log["rounds"] = round_ + 1
        system, user = prompts.render("creative_generate", feedback=feedback, n_variants=str(n_variants), **context)
        new = {key(v): v for v in generate(provider, system, user, CreativeSet, temperature=0.7).variants}
        if not variants:
            wanted = [(m.persona_id, i) for m in selected for i in range(1, n_variants + 1)]
            variants = [new[k] for k in wanted if k in new]
        else:
            rewritten = {tuple(k) for k in log["rewritten"]}
            variants = [new.get(key(v), v) if key(v) in rewritten else v for v in variants]

        system, user = prompts.render(
            "creative_review", claims=context["claims"], personas=personas,
            variants=json.dumps([v.model_dump() for v in variants], indent=1),
        )
        reviews = generate(provider, system, user, ReviewSet, temperature=0.0).reviews
        review_by_key = {key(r): r for r in reviews}
        for v in variants:
            r = review_by_key.get(key(v))
            rules = rule_check(v)
            if r and rules:
                r.issues.extend(rules)
                r.passed = False

        failed = [r for r in reviews if not r.passed]
        if not failed or round_ == 1:
            break
        log["rewritten"] = [list(key(r)) for r in failed]
        feedback = "Rewrite ONLY these variants (keep their persona_id and variant number), fixing the reviewer's issues:\n" + "\n".join(
            f"- {r.persona_id} variant {r.variant}: issues={r.issues}; unsupported_claims={r.unsupported_claims}" for r in failed
        )
    return variants, reviews, log
