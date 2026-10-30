"""Pairwise training labels for the scorer: LLM judge (both orders) + a CSV for human spot checks.

For each holdout advertiser that gets a plan, sample host pairs (some near the top of the current
ranking, some anywhere) and ask the judge "which is the better placement?" twice with the order swapped.
Kept: answers that agree across orders (position-bias filter). The agreement rate is reported: it's the
judge's self-consistency, an upper bound on how much to trust these labels.

    python -m disco_planner.eval.pairwise_labels [--pairs-per-advertiser 6] [--workers 8]
Writes reports/pairwise_labels.jsonl and reports/human_labels_todo.csv (40 pairs, blank answer column).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from disco_planner.data import catalog
from disco_planner.llm import prompts
from disco_planner.llm.provider import default_provider, generate
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
REPORTS = catalog.REPO_ROOT / "reports"


class PairJudgment(BaseModel):
    winner: Literal["A", "B", "tie"]
    confidence: int = Field(ge=1, le=5)
    reason: str


def host_card(pid: str) -> str:
    p = catalog.publisher_by_id()[pid]
    a = p.audience
    return (f"{p.name}: sells {p.category} ({', '.join(p.subcategories)}); typical order ${p.avg_order_value_usd:.0f}; "
            f"shoppers aged {a.age_skew}, {a.gender_split.get('female', 0):.0%} female, income {a.income_tier}, "
            f"mainly {', '.join(a.top_geos)}; {p.monthly_impressions / 1e6:.1f}M monthly views. Notes: {p.notes}")


def load_holdout() -> list[tuple[str, str, CampaignPlan]]:
    out = []
    for path in sorted((REPORTS / "holdout_plans").glob("*.json")):
        plan = CampaignPlan.model_validate_json(path.read_text())
        if plan.profile.gate in ("proceed", "clarify"):
            out.append((path.stem, plan.brief, plan))
    return out


def sample_pairs(plan: CampaignPlan, k: int, rng: random.Random) -> list[tuple[str, str]]:
    ranked = [s.publisher_id for s in rank_publishers(plan.profile, TODAY)]  # current scorer, from saved profile
    linked = [s.publisher_id for s in rank_publishers(plan.profile, TODAY) if s.components[0].value >= 0.2]
    pairs = set()
    tries = 0
    while len(pairs) < k and tries < 200:
        tries += 1
        kind = len(pairs) % 3
        if kind == 0:     # top pick vs one of ranks 2-6
            a, b = ranked[0], rng.choice(ranked[1:6])
        elif kind == 1 and len(linked) >= 2:   # two hosts with a real category link
            a, b = rng.sample(linked, 2)
        else:             # anywhere in the catalog
            a, b = rng.sample(ranked, 2)
        pairs.add(tuple(sorted((a, b))))
    return list(pairs)


def judge(provider, brief: str, a: str, b: str) -> PairJudgment:
    system, user = prompts.render("pairwise_judge", brief=brief, host_a=host_card(a), host_b=host_card(b))
    return generate(provider, system, user, PairJudgment, temperature=0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs-per-advertiser", type=int, default=6)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    rng = random.Random(5)
    provider = default_provider()

    jobs = []
    for adv_id, brief, plan in load_holdout():
        for a, b in sample_pairs(plan, args.pairs_per_advertiser, rng):
            jobs.append((adv_id, brief, a, b))

    def work(job):
        adv_id, brief, a, b = job
        try:
            ab, ba = judge(provider, brief, a, b), judge(provider, brief, b, a)
        except Exception as e:  # a failed judgement is dropped, not fatal
            return {"advertiser": adv_id, "a": a, "b": b, "error": str(e)[:120]}
        # ab says winner A/B in (a, b) order; ba is in (b, a) order, so map it back
        ba_mapped = {"A": "B", "B": "A", "tie": "tie"}[ba.winner]
        consistent = ab.winner == ba_mapped
        return {"advertiser": adv_id, "a": a, "b": b, "ab": ab.winner, "ba": ba_mapped, "consistent": consistent,
                "label": ab.winner if consistent else None, "confidence": min(ab.confidence, ba.confidence),
                "reason": ab.reason}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(work, jobs))

    REPORTS.mkdir(exist_ok=True)
    with (REPORTS / "pairwise_labels.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    ok = [r for r in rows if "error" not in r]
    consistent = [r for r in ok if r["consistent"]]
    decisive = [r for r in consistent if r["label"] != "tie"]
    print(f"{len(rows)} pairs judged twice ({len(rows) - len(ok)} errors); order-consistent {len(consistent)}/{len(ok)} "
          f"({len(consistent) / max(1, len(ok)):.0%}); decisive (non-tie) {len(decisive)}")

    # human spot-check sheet: 40 random decisive pairs, judge answer hidden
    briefs = {adv_id: brief for adv_id, brief, _ in load_holdout()}
    sample = rng.sample(decisive, min(40, len(decisive)))
    with (REPORTS / "human_labels_todo.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair_id", "advertiser_brief", "host_A", "host_B", "your_choice (A/B/tie)"])
        for i, r in enumerate(sample):
            w.writerow([f"{r['advertiser']}:{r['a']}:{r['b']}", briefs[r["advertiser"]], host_card(r["a"]), host_card(r["b"]), ""])
    print(f"wrote {REPORTS / 'human_labels_todo.csv'} ({len(sample)} pairs for a human check)")


if __name__ == "__main__":
    main()
