"""Compare scoring-rule variants (config patches) on the golden set and the holdout dev / test brands.

    python -m disco_planner.eval.compare_rules [--include-test]

Variants are dotted-path config overrides applied in memory (see VARIANTS below). For each variant:
  golden   precision / must-include recall@5 / must-exclude violations / hosts per plan   (15 briefs, dev set)
  dev      judge preference: of pairs where exactly one host is recommended, how often the LLM judge prefers it;
           ranking agreement on all decisive pairs                                         (49 holdout dev brands)
  test     the same on the 51 holdout TEST brands: printed only with --include-test, which should be used ONCE
           per decision, after the variant was chosen on golden + dev.
Caveat printed with the results: the judge and golden labels are category-minded, so they under-reward rules that
recommend across categories; only click / conversion outcomes can settle those.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
from datetime import date
from pathlib import Path

import yaml

from disco_planner.data import catalog
from disco_planner.eval.sensitivity import evaluate
from disco_planner.llm.provider import default_provider
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
EVAL = catalog.REPO_ROOT / "src/disco_planner/eval"
REPORTS = catalog.REPO_ROOT / "reports"

# name -> {"file.dotted.path": value}
VARIANTS: dict[str, dict[str, object]] = {
    "hard gate (topic only)": {"scoring.relevance.audience_evidence_min": 99.0},
    "soft: topic OR stated audience": {},
}


def apply(patch: dict) -> dict:
    saved = {}
    for key, value in patch.items():
        name, *path = key.split(".")
        cfg = catalog.config(name)
        saved.setdefault(name, copy.deepcopy(cfg))
        node = cfg
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    return saved


def restore(saved: dict) -> None:
    for name, original in saved.items():
        cfg = catalog.config(name)
        cfg.clear()
        cfg.update(original)


def judge_stats(plans: dict[str, CampaignPlan], labels: list[dict]) -> tuple[float, int, float, int, int]:
    agree = n = order = on = led = 0
    for pid, plan in plans.items():
        ranked = rank_publishers(plan.profile, TODAY)
        rec = {s.publisher_id for s in ranked if s.recommended}
        led += sum(1 for s in ranked if s.recommended and s.relevance_source == "audience")
        score = {s.publisher_id: s.score for s in ranked}
        for l in labels:
            if l["advertiser"] != pid:
                continue
            on += 1
            order += (score[l["a"]] > score[l["b"]]) == (l["label"] == "A")
            if (l["a"] in rec) != (l["b"] in rec):
                n += 1
                agree += (l["label"] == "A") == (l["a"] in rec)
    return agree / max(n, 1), n, order / max(on, 1), on, led


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-test", action="store_true")
    args = ap.parse_args()
    golden = {c["id"]: c for c in yaml.safe_load((EVAL / "golden.yaml").read_text())}
    provider = default_provider()
    gprofiles = {cid: parse_brief(c["text"], provider, TODAY) for cid, c in golden.items()}
    split = yaml.safe_load((EVAL / "holdout_split.yaml").read_text())
    labels = [json.loads(l) for l in (REPORTS / "pairwise_labels.jsonl").read_text().splitlines()]
    labels = [l for l in labels if l.get("label") in ("A", "B")]
    all_plans = {p.stem: CampaignPlan.model_validate_json(p.read_text()) for p in (REPORTS / "holdout_plans").glob("*.json")}
    groups = {"dev": split["dev"]}
    if args.include_test:
        groups["test"] = split["test"]

    header = f"{'variant':34} {'gold prec':>9} {'recall':>6} {'viol':>4} {'hosts':>5}"
    for g in groups:
        header += f" | {g + ' judge pref':>14} {'(n)':>5} {g + ' order':>10} {'audience-led recs':>18}"
    print(header)
    for name, patch in VARIANTS.items():
        saved = apply(patch)
        try:
            g = evaluate(gprofiles, golden, None)
            viol = sum(len(set(golden[c].get("must_exclude") or []) & set(r)) for c, r in g["recs"].items())
            hosts = statistics.mean(len(r) for r in g["recs"].values())
            row = f"{name:34} {g['precision']:>9.3f} {g['recall']:>6.2f} {viol:>4} {hosts:>5.2f}"
            for grp, ids in groups.items():
                plans = {i: all_plans[i] for i in ids if i in all_plans and all_plans[i].profile.gate in ("proceed", "clarify")}
                jp, n, oa, on, led = judge_stats(plans, [l for l in labels if l["advertiser"] in plans])
                row += f" | {jp:>14.1%} {n:>5} {oa:>10.1%} {led:>18}"
            print(row)
        finally:
            restore(saved)
    print("\nCaveat: the judge and golden labels are category-minded; they under-reward cross-category recommendations.")


if __name__ == "__main__":
    main()
