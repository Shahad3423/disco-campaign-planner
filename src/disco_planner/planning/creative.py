"""Stage 3: persona-tuned creatives = draft candidates -> rule checks + LLM review -> keep the best -> one rewrite.

Write many, keep the best: the writer drafts candidates_per_persona lines per persona with different hooks;
code keeps the variants_per_persona highest-scoring passing ones (punch, then persona fit, distinct hooks).
Two independent gates, because each catches what the other misses:
- rule checks (code): lengths, placeholder count, banned and generic words. Cheap, exact.
- LLM review (separate call): punch, persona fit, unsupported claims, distinctness. Semantic.
"""
from __future__ import annotations

import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor

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
    cfg = catalog.config("scoring")["creative"]
    issues = []
    headline_len = len(v.headline.replace(PLACEHOLDER, "x" * NAME_LEN))
    if headline_len > cfg["max_headline"]:
        issues.append(f"headline {headline_len} chars > {cfg['max_headline']}")
    if len(v.headline_no_name) > cfg["max_headline"]:
        issues.append(f"headline_no_name {len(v.headline_no_name)} chars > {cfg['max_headline']}")
    if len(v.body) > cfg["max_body"]:
        issues.append(f"body {len(v.body)} chars > {cfg['max_body']}")
    if len(v.cta) > cfg["max_cta"]:
        issues.append(f"cta {len(v.cta)} chars > {cfg['max_cta']}")
    text = f"{v.headline} {v.body} {v.cta}".lower()
    generic = [w for w in cfg["banned_words"] if re.search(rf"\b{re.escape(w)}\b", text)]
    if generic:
        issues.append(f"generic or corporate wording: {generic}")
    if not cfg["use_first_name"] and PLACEHOLDER in v.headline:
        issues.append("uses {first_name}, but personalisation by name is switched off")
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
    """Returns variants, their final reviews, and a small log of what happened (for evals/UI).
    Each persona gets its own write -> review -> (rewrite) cycle and the cycles run in parallel: a call that writes
    for one persona produces a fraction of the text, so it is faster, and no persona waits for another's review."""
    context = dict(
        summary=profile.business_summary, product=profile.product,
        claims=json.dumps(profile.stated_claims), values=json.dumps(profile.values),
        fulfillment=profile.fulfillment_notes or "none stated",
        moments=", ".join(moments) or "none",
        name_rule=("`headline` may contain the literal placeholder {first_name} once, only where a friend would say "
                   "the name. Never write a real name. `headline_no_name` is the same line without it."
                   if catalog.config("scoring")["creative"]["use_first_name"] else
                   "never use the shopper's name or any placeholder. Set `headline_no_name` to exactly the same text as `headline`."),
    )
    # Threads don't inherit context variables (the app's live/cache toggle and call counter), so each task runs in a copy.
    with ThreadPoolExecutor(max_workers=max(1, len(selected))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _creatives_for_persona, m, context, provider) for m in selected]
        results = [f.result() for f in futures]  # in persona order; a failure re-raises here
    variants = [v for r in results for v in r[0]]
    reviews = [x for r in results for x in r[1]]
    log = {"rounds": max((r[2]["rounds"] for r in results), default=0),
           "rewritten": [k for r in results for k in r[2]["rewritten"]],
           "candidates": sum(r[2]["candidates"] for r in results)}
    return variants, reviews, log


def _creatives_for_persona(
    m: PersonaMatch, context: dict, provider: Provider,
) -> tuple[list[CreativeVariant], list[CreativeReview], dict]:
    """Draft candidates for ONE persona and keep the best, then at most one rewrite of failures."""
    personas = _persona_block([m])
    cfg = catalog.config("scoring")["personas"]
    n_variants, n_candidates = cfg["variants_per_persona"], max(cfg["variants_per_persona"], cfg["candidates_per_persona"])
    key = lambda x: (x.persona_id, x.variant)
    mine = lambda x: x.persona_id == m.persona_id  # a model can drift onto another persona's id; ignore that
    log = {"rounds": 0, "rewritten": [], "candidates": 0}
    feedback = ""
    variants: list[CreativeVariant] = []
    reviews: list[CreativeReview] = []

    def review(batch: list[CreativeVariant]) -> dict:
        system, user = prompts.render(
            "creative_review", claims=context["claims"], personas=personas,
            variants=json.dumps([v.model_dump() for v in batch], indent=1),
        )
        out = {key(r): r for r in generate(provider, system, user, ReviewSet, temperature=0.0).reviews if mine(r)}
        for v in batch:
            r, rules = out.get(key(v)), rule_check(v)
            if r and rules:
                r.issues.extend(rules)
                r.passed = False
        return out

    for round_ in range(2):
        log["rounds"] = round_ + 1
        system, user = prompts.render("creative_generate", feedback=feedback, n_variants=str(n_variants),
                                      n_candidates=str(n_candidates if not variants else n_variants),
                                      personas=personas, **context)
        new = {key(v): v for v in generate(provider, system, user, CreativeSet, temperature=0.7).variants if mine(v)}
        if not variants:
            drafts = list(new.values())
            log["candidates"] = len(drafts)
            by_key = review(drafts)
            own = sorted((v for v in drafts if key(v) in by_key),
                         key=lambda v: (not by_key[key(v)].passed, -(by_key[key(v)].punch or 0), -by_key[key(v)].persona_fit))
            kept, hooks = [], set()
            for v in own:  # best first; a second line must use a different hook
                hook = v.angle.split(":")[0].split(" ")[0].lower()
                if len(kept) < n_variants and hook not in hooks:
                    kept.append(v)
                    hooks.add(hook)
            kept += [v for v in own if v not in kept][: n_variants - len(kept)]
            for i, v in enumerate(kept, start=1):
                r = by_key[key(v)]
                v.variant = r.variant = i
                variants.append(v)
                reviews.append(r)
        else:
            rewritten = {tuple(k) for k in log["rewritten"]}
            variants = [new.get(key(v), v) if key(v) in rewritten else v for v in variants]
            by_key = review(variants)
            reviews = [by_key.get(key(v)) for v in variants if by_key.get(key(v))]

        failed = [r for r in reviews if not r.passed]
        if not failed or round_ == 1:
            break
        log["rewritten"] = [list(key(r)) for r in failed]
        feedback = "Rewrite ONLY these variants (keep their persona_id and variant number), fixing the reviewer's issues:\n" + "\n".join(
            f"- {r.persona_id} variant {r.variant}: issues={r.issues}; unsupported_claims={r.unsupported_claims}" for r in failed
        )
    return variants, reviews, log
