"""Whole-pipeline before/after report: one table per stage.

"Before" = artifacts produced with the previous scoring (competitor penalty, hand-set exponents);
"after" = re-runs with the current code (LLM outputs come from the cache, so differences are the code's).

    python -m disco_planner.eval.pipeline_report --before-golden reports/eval_v3c_bids_goals.json \
        --after-golden reports/eval_v5.json --before-holdout reports/holdout_before.json \
        --after-holdout reports/holdout.json --before-plans reports/holdout_plans_before
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from disco_planner.data import catalog
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.insights import auction_insights

REPORTS = catalog.REPO_ROOT / "reports"


def load_plans(folder: Path) -> list[CampaignPlan]:
    out = []
    for p in sorted(folder.glob("*.json")):
        try:
            out.append(CampaignPlan.model_validate_json(p.read_text()))
        except Exception:
            continue
    return out


def competitor_stats(plans: list[CampaignPlan]) -> dict:
    live = [p for p in plans if p.profile.gate in ("proceed", "clarify")]
    pairs = [(p, s) for p in live for s in p.publishers if s.recommended]
    flagged = [x for x in pairs if any(f.startswith("competitor") for f in x[1].flags)]
    top1 = [p for p in live if (r := [s for s in p.publishers if s.recommended]) and any(f.startswith("competitor") for f in r[0].flags)]
    allp = sum(1 for p in live for s in p.publishers if any(f.startswith("competitor") for f in s.flags))
    return {"flagged_share_of_recommended": round(len(flagged) / max(1, len(pairs)), 3),
            "advertisers_with_a_flag": round(sum(any(any(f.startswith("competitor") for f in s.flags) for s in p.publishers if s.recommended) for p in live) / max(1, len(live)), 3),
            "top_pick_is_flagged": round(len(top1) / max(1, len(live)), 3),
            "flagged_share_of_all_pairs": round(allp / max(1, len(live) * len(catalog.publishers())), 3),
            "mean_hosts_recommended": round(statistics.mean(len([s for s in p.publishers if s.recommended]) for p in live), 2) if live else 0}


def auction_stats(plans: list[CampaignPlan], rivals: list[CampaignPlan]) -> dict:
    rates = []
    for plan in plans:
        if plan.config.status == "draft" and plan.config.budget and plan.config.budget.allocations and plan.creatives:
            rates += [i.win_rate for i in auction_insights(plan, [r for r in rivals if r is not plan])]
    if not rates:
        return {}
    return {"host_slots": len(rates), "mean_win_rate": round(statistics.mean(rates), 3),
            "share_at_0": round(sum(r == 0 for r in rates) / len(rates), 3),
            "share_at_100": round(sum(r == 1 for r in rates) / len(rates), 3),
            "share_mixed": round(sum(0 < r < 1 for r in rates) / len(rates), 3)}


def row(name, before, after, fmt="{}"):
    b = "-" if before is None else fmt.format(before)
    a = "-" if after is None else fmt.format(after)
    delta = ""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        delta = f"{after - before:+.3f}"
    return f"| {name} | {b} | {a} | {delta} |"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before-golden", type=Path, required=True)
    ap.add_argument("--after-golden", type=Path, required=True)
    ap.add_argument("--before-holdout", type=Path, required=True)
    ap.add_argument("--after-holdout", type=Path, required=True)
    ap.add_argument("--before-plans", type=Path, required=True)
    args = ap.parse_args()

    bg, ag = (json.loads(p.read_text()) for p in (args.before_golden, args.after_golden))
    bh, ah = (json.loads(p.read_text())["summary"] for p in (args.before_holdout, args.after_holdout))
    before_plans, after_plans = load_plans(args.before_plans), load_plans(REPORTS / "holdout_plans")
    demo = load_plans(catalog.REPO_ROOT / "demo_cache" / "plans")
    cb, ca = competitor_stats(before_plans), competitor_stats(after_plans)
    auction_after = auction_stats(demo, demo + after_plans)

    g = lambda d, k: d["aggregate"].get(k)
    gr = lambda d, k: (d.get("robustness") or {}).get(k)
    lines = ["# Whole-pipeline evaluation: before vs after the scoring changes", "",
             "Changes: competitor = flag + host setting (no score/budget penalty, majority flags, fallback host); "
             "constants in config; power-mean aggregation; exponents fitted from pairwise labels (relevance 0.8, price 0.6).", "",
             "## Golden set (15 examples, blind labels)", "", "| metric | before | after | delta |", "|---|---|---|---|"]
    for k in ("gate_accuracy", "must_include_recall@5", "must_exclude_violations", "precision", "competitor_flag_recall",
              "implausible_personas_selected", "plausible_persona_recall", "budget_invariants_ok",
              "creative_rule_pass_rate", "creative_review_pass_rate", "creative_mean_persona_fit", "creative_unsupported_claims"):
        lines.append(row(k, g(bg, k), g(ag, k)))
    lines.append(row("paraphrase stability (top-5 Jaccard)", gr(bg, "paraphrase_mean_jaccard"), gr(ag, "paraphrase_mean_jaccard")))
    lines.append(row("prompt-injection pass rate", gr(bg, "injection_pass_rate"), gr(ag, "injection_pass_rate")))
    lines += ["", "## Holdout (100 real brands, blind)", "", "| metric | before | after | delta |", "|---|---|---|---|",
              row("gate agreement with writer's expectation", bh.get("gate_agreement_with_expected"), ah.get("gate_agreement_with_expected")),
              row("errors", bh.get("errors"), ah.get("errors"))]
    for seg in sorted(set(bh.get("segments", {})) | set(ah.get("segments", {}))):
        sb, sa = bh["segments"].get(seg, {}), ah["segments"].get(seg, {})
        lines.append(row(f"{seg}: mean hosts recommended", sb.get("mean_recommended"), sa.get("mean_recommended")))
        lines.append(row(f"{seg}: weak-fit rate when planned", sb.get("weak_fit_rate_when_planned"), sa.get("weak_fit_rate_when_planned")))
    lines.append(row("hosts never recommended (count)", len(bh.get("hosts_never_recommended", [])), len(ah.get("hosts_never_recommended", []))))
    top_b = max(bh.get("host_recommendation_share", {"-": 0}).values()); top_a = max(ah.get("host_recommendation_share", {"-": 0}).values())
    lines.append(row("most-recommended host's share of plans", top_b, top_a))
    lines += ["", "## Competitor flags (holdout plans)", "", "| metric | before | after | delta |", "|---|---|---|---|"]
    for k in cb:
        lines.append(row(k, cb[k], ca.get(k)))
    lines += ["", "## Auctions (one slot per page; 15 demo campaigns vs demo + holdout rivals)", "",
              "| metric | after |", "|---|---|"] + [f"| {k} | {v} |" for k, v in auction_after.items()]
    out = REPORTS / "pipeline_report.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
