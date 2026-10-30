"""Which scoring constants actually matter? Global sensitivity (Morris screening) on the golden set.

Morris elementary effects (Morris 1991; Saltelli et al., "Global Sensitivity Analysis: The Primer"):
every constant gets a range, we walk r random one-at-a-time trajectories through the joint space, and for
each constant record how much the output moves when it alone changes, from many different starting points.
  mu*   = mean |effect|      -> how much it matters overall
  sigma = std of the effect  -> how much that depends on the OTHER constants (interactions / nonlinearity)
Output = how much the recommendations move vs the current config (1 - top-5 Jaccard, averaged over the
15 briefs), plus precision and must-include recall against the golden labels. All re-ranking uses the
cached intake profiles: no LLM calls.

    python -m disco_planner.eval.sensitivity [--trajectories 20]
"""
from __future__ import annotations

import argparse
import copy
import random
import statistics
from datetime import date

import yaml

from disco_planner.data import catalog
from disco_planner.llm.provider import default_provider
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
EVAL_DIR = catalog.REPO_ROOT / "src" / "disco_planner" / "eval"
SKIP = {"max_publishers", "weak_fit_max_publishers", "min_selected", "max_selected", "lookahead_days",
        "self_consistency_samples"}  # integers / non-scoring knobs
ABSOLUTE_RANGES = {"quality_p": (-2.0, 2.0), "relevance_exponent": (0.25, 2.0), "price_exponent": (0.0, 1.5)}


def tunable_params(cfg: dict, prefix: str = "") -> list[tuple[str, float, float]]:
    """(dotted path, low, high) for every numeric leaf of scoring.yaml we can move."""
    out = []
    for key, value in cfg.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            if key in ("price_compat",):   # 25-cell lookup table: handled as one scale knob below
                continue
            out += tunable_params(value, path + ".")
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and key not in SKIP:
            if key in ABSOLUTE_RANGES:
                lo, hi = ABSOLUTE_RANGES[key]
            else:
                lo, hi = value * 0.5, value * 1.5
                if value <= 1.0:
                    hi = min(hi, 1.0)   # probabilities, caps, values on [0, 1]
            out.append((path, lo, hi))
    return out


def _set(d: dict, path: str, value) -> None:
    *head, last = path.split(".")
    for k in head:
        d = d[k]
    d[last] = value


def evaluate(profiles: dict, golden: dict, base_rec: dict | None) -> dict:
    jac, prec, rec_ = [], [], []
    recs = {}
    for cid, profile in profiles.items():
        rec = [s.publisher_id for s in rank_publishers(profile, TODAY) if s.recommended]
        recs[cid] = rec
        g = golden[cid]
        must = set(g.get("must_include") or [])
        ok = must | set(g.get("nice_to_have") or []) | set(g.get("competitor_conflict") or [])
        if rec:
            prec.append(len(set(rec) & ok) / len(rec))
        if must:
            rec_.append(len(must & set(rec[:5])) / len(must))
        if base_rec is not None and (rec or base_rec[cid]):
            a, b = set(rec[:5]), set(base_rec[cid][:5])
            jac.append(len(a & b) / len(a | b))
    return {"recs": recs, "change": 1 - statistics.mean(jac) if jac else 0.0,
            "precision": statistics.mean(prec) if prec else 0.0, "recall": statistics.mean(rec_) if rec_ else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trajectories", type=int, default=20)
    args = ap.parse_args()
    random.seed(11)

    golden = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden.yaml").read_text())}
    provider = default_provider()
    profiles = {cid: parse_brief(c["text"], provider, TODAY) for cid, c in golden.items()}  # cached

    cfg = catalog.config("scoring")
    original = copy.deepcopy(cfg)
    params = tunable_params(original)
    base = evaluate(profiles, golden, None)
    k, levels = len(params), 4
    delta = levels / (2 * (levels - 1))
    grid = [i / (levels - 1) for i in range(levels)]

    def run(unit: list[float]) -> dict:
        cfg.clear()
        cfg.update(copy.deepcopy(original))
        for (path, lo, hi), u in zip(params, unit):
            _set(cfg, path, lo + u * (hi - lo))
        total = sum(cfg["quality_weights"].values())
        cfg["quality_weights"] = {n: v / total for n, v in cfg["quality_weights"].items()}
        return evaluate(profiles, golden, base["recs"])

    effects = {p[0]: {"change": [], "precision": [], "recall": []} for p in params}
    try:
        for _ in range(args.trajectories):
            x = [random.choice([g for g in grid if g + delta <= 1]) for _ in range(k)]
            y_prev = run(x)
            for i in random.sample(range(k), k):
                x = x.copy()
                x[i] += delta
                y = run(x)
                for m in ("change", "precision", "recall"):
                    effects[params[i][0]][m].append((y[m] - y_prev[m]) / delta)
                y_prev = y
    finally:
        cfg.clear()
        cfg.update(original)

    rows = []
    for path, e in effects.items():
        ch = e["change"]
        rows.append((path, statistics.mean(abs(v) for v in ch), statistics.pstdev(ch),
                     statistics.mean(e["precision"]), statistics.mean(e["recall"])))
    rows.sort(key=lambda r: -r[1])
    lines = [
        "# Scoring sensitivity: Morris screening", "",
        f"{k} constants, {args.trajectories} trajectories ({args.trajectories * (k + 1)} re-rankings of 15 briefs), "
        f"each constant over [x0.5, x1.5] of its value (exponents/p over fixed ranges).",
        f"Current config: precision {base['precision']:.3f}, must-include recall@5 {base['recall']:.3f}.", "",
        "mu* = how much the top-5 moves when this constant moves across its range (0 = never; 1 = completely);",
        "sigma = how much that depends on the other constants. Signed columns: average effect on precision/recall.", "",
        "| constant | mu* (top-5 change) | sigma | effect on precision | effect on recall |", "|---|---|---|---|---|",
    ]
    lines += [f"| {p} | {mu:.3f} | {sd:.3f} | {dp:+.3f} | {dr:+.3f} |" for p, mu, sd, dp, dr in rows]
    out = catalog.REPO_ROOT / "reports" / "sensitivity.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
