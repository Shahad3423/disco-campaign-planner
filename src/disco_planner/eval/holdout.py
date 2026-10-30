"""Holdout check on 100 real-brand briefs written blind to the scoring code.

No golden publisher labels exist here, so we test *behaviour* that should hold for any
advertiser, which is exactly what overfitting to the 15 examples would break:
  - gates: B2B/local/vague briefs get off_network / clarify, not confident plans
  - honesty: out-of-catalog products get weak-fit or small-test answers more often
  - calibration: confidence is lower for vague briefs
  - concentration: a few hosts shouldn't be recommended for everything
  - robustness: no crashes; sensitive categories don't get regulated claims in copy

    python -m disco_planner.eval.holdout [--creatives-sample 15]
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from datetime import date

import yaml

from disco_planner.data import catalog
from disco_planner.llm.provider import default_provider
from disco_planner.planning import claims
from disco_planner.planning.creative import rule_check
from disco_planner.planning.pipeline import run

EVAL_DIR = catalog.REPO_ROOT / "src" / "disco_planner" / "eval"
TODAY = date(2026, 10, 30)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creatives-sample", type=int, default=0)
    args = ap.parse_args()

    cases = yaml.safe_load((EVAL_DIR / "holdout_advertisers.yaml").read_text())
    random.seed(7)
    with_creatives = {c["id"] for c in random.sample(cases, min(args.creatives_sample, len(cases)))}
    provider = default_provider()
    rows, start = [], time.time()
    for c in cases:
        t0 = time.time()
        try:
            plan = run(c["text"], provider, TODAY, with_creatives=c["id"] in with_creatives)
        except Exception as e:  # record, don't stop: crash rate is a metric
            rows.append({**c, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            print(f"  {c['id']} ERROR {type(e).__name__}", flush=True)
            continue
        rec = [s for s in plan.publishers if s.recommended]
        copy = " ".join(f"{v.headline} {v.body}" for v in plan.creatives)
        rows.append({
            **c,
            "gate": plan.profile.gate,
            "confidence": plan.profile.confidence,
            "agreement": plan.profile.sample_agreement,
            "n_recommended": len(rec),
            "recommended": [s.publisher_id for s in rec],
            "weak_fit": any(any(f.startswith("weak_network_fit") for f in s.flags) for s in rec),
            "competitor_flagged": any(any(f.startswith("competitor") for f in s.flags) for s in rec),
            "n_creatives": len(plan.creatives),
            "creative_rule_fail": sum(bool(rule_check(v)) for v in plan.creatives),
            "creative_review_fail": sum(not r.passed for r in plan.reviews),
            "regulated_in_copy": bool(claims.violations(copy)) if copy else False,
            "blocked_claims": plan.profile.claims_needing_substantiation,
            "seconds": round(time.time() - t0, 1),
        })
        print(f"  {c['id']} {plan.profile.gate:18} recs={len(rec)}", flush=True)

    ok = [r for r in rows if "error" not in r]
    by_segment = defaultdict(list)
    for r in ok:
        by_segment[r["segment"]].append(r)

    host_counts = Counter(p for r in ok for p in r["recommended"])
    planned = [r for r in ok if r["n_recommended"]]
    summary = {
        "n": len(rows), "errors": len(rows) - len(ok),
        "gate_agreement_with_expected": _mean([r["gate"] == r["expected_gate"] for r in ok]),
        "segments": {
            seg: {
                "n": len(rs),
                "gates": dict(Counter(r["gate"] for r in rs)),
                "mean_confidence": _mean([r["confidence"] for r in rs]),
                "mean_recommended": _mean([r["n_recommended"] for r in rs]),
                "weak_fit_rate_when_planned": _mean([r["weak_fit"] for r in rs if r["n_recommended"]]),
            }
            for seg, rs in sorted(by_segment.items())
        },
        "confidence_by_clarity": {
            cl: _mean([r["confidence"] for r in ok if r["clarity"] == cl]) for cl in ("clear", "medium", "vague")
        },
        "host_recommendation_share": {
            catalog.publisher_by_id()[p].name: round(n / max(1, len(planned)), 2) for p, n in host_counts.most_common()
        },
        "hosts_never_recommended": sorted(
            catalog.publisher_by_id()[p.id].name for p in catalog.publishers() if p.id not in host_counts
        ),
        "creatives": {
            "plans_with_creatives": sum(1 for r in ok if r["n_creatives"]),
            "rule_fail": sum(r["creative_rule_fail"] for r in ok),
            "review_fail": sum(r["creative_review_fail"] for r in ok),
            "regulated_claims_in_copy": sum(r["regulated_in_copy"] for r in ok),
            "briefs_with_blocked_claims": sum(bool(r["blocked_claims"]) for r in ok),
        },
        "wall_time_s": round(time.time() - start, 1),
    }
    gate_mismatches = [
        (r["id"], r.get("brand"), r["expected_gate"], r["gate"], r["text"][:90]) for r in ok if r["gate"] != r["expected_gate"]
    ]

    out = catalog.REPO_ROOT / "reports"
    (out / "holdout.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str))
    md = ["# Holdout: 100 real-brand briefs (blind to scoring)", "", "```json", json.dumps(summary, indent=1), "```", "",
          "## Gate mismatches vs the writer's rough expectation", "", "| id | brand | expected | got | brief |", "|---|---|---|---|---|"]
    md += [f"| {i} | {b} | {e} | {g} | {t} |" for i, b, e, g, t in gate_mismatches]
    (out / "holdout.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:5 + len(json.dumps(summary, indent=1).splitlines())]))


if __name__ == "__main__":
    main()
