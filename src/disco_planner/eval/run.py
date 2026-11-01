"""Per-stage evaluation of the planning pipeline against the blind golden set.

    python -m disco_planner.eval.run                  # matching-only (cheap: 1 LLM call/case, cached)
    python -m disco_planner.eval.run --creatives      # also generate + grade creatives
    python -m disco_planner.eval.run --robustness     # paraphrase stability + prompt injection
    python -m disco_planner.eval.run --compare reports/eval_baseline.json

Every stage gets its own metrics, so a regression points at the stage that caused it.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import time
from datetime import date
from pathlib import Path

import yaml

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserInputs
from disco_planner.llm.provider import default_provider
from disco_planner.planning.creative import rule_check
from disco_planner.planning.pipeline import CampaignPlan, run

EVAL_DIR = Path(__file__).parent
REPORT_DIR = catalog.REPO_ROOT / "reports"
TODAY = date(2026, 10, 30)
# D23: budgets come from the advertiser; evals use one stated budget so budget invariants are checked
EVAL_INPUTS = AdvertiserInputs(monthly_budget_usd=5000)
ADJACENT_GATES = {frozenset({"proceed", "clarify"}), frozenset({"clarify", "reject_low_signal"})}


def _recommended(plan: CampaignPlan) -> list[str]:
    return [s.publisher_id for s in plan.publishers if s.recommended]


def _flagged_competitors(plan: CampaignPlan) -> set[str]:
    return {s.publisher_id for s in plan.publishers if any("competitor" in f for f in s.flags)}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def grade_case(case: dict, plan: CampaignPlan) -> dict:
    rec = _recommended(plan)
    top5 = rec[:5]
    must, nice = set(case.get("must_include") or []), set(case.get("nice_to_have") or [])
    excl, comp = set(case.get("must_exclude") or []), set(case.get("competitor_conflict") or [])
    selected = {m.persona_id for m in plan.personas if m.selected}
    plausible, implausible = set(case.get("plausible_personas") or []), set(case.get("implausible_personas") or [])
    gate, expected = plan.profile.gate, case["expected_gate"]

    m = {
        "id": case["id"],
        "difficulty": case.get("difficulty"),
        # intake
        "gate": gate, "expected_gate": expected, "gate_ok": gate == expected,
        "gate_adjacent": frozenset({gate, expected}) in ADJACENT_GATES,
        # matching
        "recommended": rec,
        "must_include_recall": len(must & set(top5)) / len(must) if must else None,
        "must_exclude_violations": sorted(excl & set(rec)),
        "precision": len(set(rec) & (must | nice | comp)) / len(rec) if rec else None,
        "competitor_flag_recall": len(comp & _flagged_competitors(plan)) / len(comp) if comp else None,
        # personas
        "personas_selected": sorted(selected),
        "implausible_selected": sorted(selected & implausible),
        "plausible_recall": len(selected & plausible) / len(plausible) if plausible else None,
    }
    if plan.config.budget:
        b = plan.config.budget
        spent = sum(a.budget_usd for a in b.allocations)
        m["budget_invariants_ok"] = (
            spent <= b.monthly_budget_usd + 0.01 and b.planned_spend_usd <= b.monthly_budget_usd + 0.01
            and all(a.budget_usd <= a.capacity_usd + 0.01 for a in b.allocations)
            and {a.publisher_id for a in b.allocations if not a.exploration} <= set(rec)
        )
    if plan.creatives:
        reviews = {(r.persona_id, r.variant): r for r in plan.reviews}
        pairs = list(itertools.combinations(plan.creatives, 2))
        jaccard = [
            len(_words(a.body) & _words(b.body)) / max(1, len(_words(a.body) | _words(b.body))) for a, b in pairs
        ]
        m.update({
            "n_creatives": len(plan.creatives),
            "rule_pass_rate": sum(not rule_check(v) for v in plan.creatives) / len(plan.creatives),
            "review_pass_rate": sum(r.passed for r in plan.reviews) / max(1, len(plan.reviews)),
            "mean_persona_fit": sum(r.persona_fit for r in plan.reviews) / max(1, len(plan.reviews)),
            "unsupported_claims": sum(len(r.unsupported_claims) for r in reviews.values()),
            "max_body_jaccard": round(max(jaccard), 2) if jaccard else 0.0,
            "max_within_persona_jaccard": round(max((j for (a, b), j in zip(pairs, jaccard) if a.persona_id == b.persona_id), default=0.0), 2),
            "variants_per_persona": round(len(plan.creatives) / max(1, len({v.persona_id for v in plan.creatives})), 2),
            "uses_placeholder": sum("{first_name}" in v.headline for v in plan.creatives),
            "rewrite_rounds": plan.creative_log.get("rounds"),
        })
    return m


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def aggregate(rows: list[dict]) -> dict:
    agg = {
        "n": len(rows),
        "gate_accuracy": _mean([r["gate_ok"] for r in rows]),
        "gate_accuracy_incl_adjacent": _mean([r["gate_ok"] or r["gate_adjacent"] for r in rows]),
        "must_include_recall@5": _mean([r["must_include_recall"] for r in rows]),
        "must_exclude_violations": sum(len(r["must_exclude_violations"]) for r in rows),
        "precision": _mean([r["precision"] for r in rows]),
        "competitor_flag_recall": _mean([r["competitor_flag_recall"] for r in rows]),
        "implausible_personas_selected": sum(len(r["implausible_selected"]) for r in rows),
        "plausible_persona_recall": _mean([r["plausible_recall"] for r in rows]),
        "budget_invariants_ok": _mean([r.get("budget_invariants_ok") for r in rows]),
    }
    if any("n_creatives" in r for r in rows):
        agg.update({
            "creative_rule_pass_rate": _mean([r.get("rule_pass_rate") for r in rows]),
            "creative_review_pass_rate": _mean([r.get("review_pass_rate") for r in rows]),
            "creative_mean_persona_fit": _mean([r.get("mean_persona_fit") for r in rows]),
            "creative_unsupported_claims": sum(r.get("unsupported_claims", 0) for r in rows),
            "creative_max_body_jaccard": _mean([r.get("max_body_jaccard") for r in rows]),
        })
    return agg


def robustness(golden: dict[int, dict], provider, plans: dict[int, CampaignPlan], creatives: bool) -> dict:
    adv = yaml.safe_load((EVAL_DIR / "adversarial.yaml").read_text())
    out = {"paraphrase": [], "injection": []}
    for p in adv["paraphrases"]:
        base = set(_recommended(plans[p["of"]])[:5])
        para = set(_recommended(run(p["text"], provider, TODAY, EVAL_INPUTS, with_creatives=False))[:5])
        out["paraphrase"].append({"of": p["of"], "top5_jaccard": round(len(base & para) / max(1, len(base | para)), 2)})
    n_pubs = len(catalog.publishers())
    for case in adv["injections"]:
        try:
            plan = run(case["text"], provider, TODAY, EVAL_INPUTS, with_creatives=creatives)
        except Exception as e:  # a crash is a failed robustness case, not a crashed eval
            out["injection"].append({"text": case["text"][:60], "passed": False, "gate": f"ERROR {type(e).__name__}"})
            continue
        exp, ok = case["expect"], True
        if exp.get("not_all_recommended"):
            ok &= len(_recommended(plan)) < n_pubs
        if "gate_in" in exp:
            ok &= plan.profile.gate in exp["gate_in"]
        if "no_claims_like" in exp:
            if not plan.creatives:  # nothing generated -> the check would pass vacuously
                out["injection"].append({"text": case["text"][:60], "passed": None, "gate": plan.profile.gate})
                continue
            text = " ".join(f"{v.headline} {v.body}" for v in plan.creatives).lower()
            ok &= not any(bad.lower() in text for bad in exp["no_claims_like"])
        out["injection"].append({"text": case["text"][:60], "passed": bool(ok), "gate": plan.profile.gate})
    out["paraphrase_mean_jaccard"] = _mean([r["top5_jaccard"] for r in out["paraphrase"]])
    out["injection_pass_rate"] = _mean([r["passed"] for r in out["injection"]])
    return out


def to_markdown(agg: dict, rows: list[dict], robust: dict | None, baseline: dict | None) -> str:
    lines = ["# Eval report", "", f"Planning date {TODAY}. Golden set: blind labels (eval/README.md).", "",
             "| metric | value |" + (" baseline | delta |" if baseline else ""),
             "|---|---|" + ("---|---|" if baseline else "")]
    for k, v in agg.items():
        row = f"| {k} | {v} |"
        if baseline and isinstance(v, (int, float)) and isinstance(baseline.get(k), (int, float)):
            row += f" {baseline[k]} | {v - baseline[k]:+.3f} |"
        elif baseline:
            row += " | |"
        lines.append(row)
    lines += ["", "| # | gate (exp) | recall@5 | excl. viol. | prec. | comp. flag | bad personas | recommended |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        gate = r["gate"] + ("" if r["gate_ok"] else f" ({r['expected_gate']})")
        lines.append(
            f"| {r['id']} | {gate} | {r['must_include_recall']} | {','.join(r['must_exclude_violations']) or '-'} | "
            f"{None if r['precision'] is None else round(r['precision'], 2)} | {r['competitor_flag_recall']} | "
            f"{','.join(r['implausible_selected']) or '-'} | {', '.join(r['recommended'])} |"
        )
    if robust:
        lines += ["", f"Paraphrase top-5 Jaccard (mean): {robust['paraphrase_mean_jaccard']}",
                  f"Prompt-injection pass rate: {robust['injection_pass_rate']}", ""]
        lines += [f"- injection `{i['text']}` -> {'SKIPPED (no creatives; run --creatives)' if i['passed'] is None else 'PASS' if i['passed'] else 'FAIL'} (gate {i['gate']})" for i in robust["injection"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creatives", action="store_true")
    ap.add_argument("--robustness", action="store_true")
    ap.add_argument("--compare", type=Path)
    ap.add_argument("--label", default="latest")
    ap.add_argument("--only", type=int, nargs="*")
    args = ap.parse_args()

    golden = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden.yaml").read_text())}
    provider = default_provider()
    rows, plans, start = [], {}, time.time()
    for cid, case in golden.items():
        if args.only and cid not in args.only:
            continue
        plans[cid] = run(case["text"], provider, TODAY, EVAL_INPUTS, with_creatives=args.creatives)
        rows.append(grade_case(case, plans[cid]))
        print(f"  graded #{cid}", flush=True)

    agg = aggregate(rows)
    agg["wall_time_s"] = round(time.time() - start, 1)
    robust = robustness(golden, provider, plans, args.creatives) if args.robustness else None
    baseline = json.loads(args.compare.read_text())["aggregate"] if args.compare else None

    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / f"eval_{args.label}.json").write_text(json.dumps({"aggregate": agg, "cases": rows, "robustness": robust}, indent=1, default=str))
    md = to_markdown(agg, rows, robust, baseline)
    (REPORT_DIR / f"eval_{args.label}.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
