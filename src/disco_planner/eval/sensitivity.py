"""Which scoring constants actually matter? One-at-a-time sensitivity on the golden set.

Each constant is moved to x0.5 and x1.5 of its value (probabilities/caps clipped to [0, 1]); all 15
briefs are re-ranked from their cached intake profiles (no LLM calls), and we measure:
  - top5_jaccard: overlap of the recommended set with the baseline's (1 = unchanged)
  - d_precision / d_recall: change in precision / must-include recall@5 vs the golden labels
Constants that barely move anything can stay as defaults; the ones that move rankings are the ones
that need data (labels now, outcomes in production).

    python -m disco_planner.eval.sensitivity
"""
from __future__ import annotations

import copy
from datetime import date

import yaml

from disco_planner.data import catalog
from disco_planner.llm.provider import default_provider
from disco_planner.planning import publisher_scoring
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
EVAL_DIR = catalog.REPO_ROOT / "src" / "disco_planner" / "eval"

# (config file, dotted path) for every tunable constant that lives in config
PARAMS = [
    ("scoring", "quality_weights.audience_fit"), ("scoring", "quality_weights.persona_fit"),
    ("scoring", "quality_weights.notes_fit"), ("scoring", "quality_weights.scale"),
    ("scoring", "quality_weights.seasonality"), ("scoring", "price_exponent"),
    ("scoring", "category.category_only_cap"), ("scoring", "category.attribute_only_cap"),
    ("scoring", "category.extra_specific_bonus"), ("scoring", "category.attribute_bonus"),
    ("scoring", "penalties.notes_misaligned"), ("scoring", "recommend.min_score"),
    ("scoring", "recommend.relative_to_top"), ("scoring", "recommend.min_category_fit"),
    ("scoring", "recommend.strong_min_price_fit"), ("scoring", "personas.min_score"),
    ("pricing", "priors.competitor_approval_probability"),
]
PROBABILITY_LIKE = {"category_only_cap", "attribute_only_cap", "notes_misaligned", "relative_to_top",
                    "min_category_fit", "strong_min_price_fit", "competitor_approval_probability"}


def _get(d: dict, path: str):
    for k in path.split("."):
        d = d[k]
    return d


def _set(d: dict, path: str, value) -> None:
    *head, last = path.split(".")
    for k in head:
        d = d[k]
    d[last] = value


def _metrics(profiles: dict, golden: dict) -> dict:
    out = {}
    for cid, profile in profiles.items():
        rec = [s.publisher_id for s in rank_publishers(profile, TODAY) if s.recommended]
        g = golden[cid]
        must, ok = set(g.get("must_include") or []), set((g.get("must_include") or []) + (g.get("nice_to_have") or []) + (g.get("competitor_conflict") or []))
        out[cid] = {
            "rec": rec,
            "recall": len(must & set(rec[:5])) / len(must) if must else None,
            "precision": len(set(rec) & ok) / len(rec) if rec else None,
        }
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def main() -> None:
    golden = {c["id"]: c for c in yaml.safe_load((EVAL_DIR / "golden.yaml").read_text())}
    provider = default_provider()
    profiles = {cid: parse_brief(c["text"], provider, TODAY) for cid, c in golden.items()}  # cached
    base = _metrics(profiles, golden)
    base_p, base_r = _mean(m["precision"] for m in base.values()), _mean(m["recall"] for m in base.values())

    rows = []
    variants = [(cfg, path, f) for cfg, path in PARAMS for f in (0.5, 1.5)]
    variants += [("code", "NEUTRAL", f) for f in (0.5, 1.5)]
    for cfg_name, path, factor in variants:
        if cfg_name == "code":
            original = publisher_scoring.NEUTRAL
            publisher_scoring.NEUTRAL = original * factor
        else:
            cfg = catalog.config(cfg_name)
            saved = copy.deepcopy(cfg)
            value = _get(cfg, path) * factor
            if path.split(".")[-1] in PROBABILITY_LIKE:
                value = min(1.0, value)
            _set(cfg, path, value)
            if path.startswith("quality_weights."):  # keep the weights summing to 1
                total = sum(cfg["quality_weights"].values())
                cfg["quality_weights"] = {k: v / total for k, v in cfg["quality_weights"].items()}
        try:
            m = _metrics(profiles, golden)
        finally:
            if cfg_name == "code":
                publisher_scoring.NEUTRAL = original
            else:
                cfg.clear()
                cfg.update(saved)
        jac = _mean(
            len(set(m[c]["rec"][:5]) & set(base[c]["rec"][:5])) / max(1, len(set(m[c]["rec"][:5]) | set(base[c]["rec"][:5])))
            for c in m if base[c]["rec"] or m[c]["rec"]
        )
        rows.append((path, factor, round(jac, 3), round(_mean(x["precision"] for x in m.values()) - base_p, 3),
                     round(_mean(x["recall"] for x in m.values()) - base_r, 3)))

    # summarise per constant: the worse of its two perturbations
    by_param = {}
    for path, f, jac, dp, dr in rows:
        cur = by_param.get(path)
        if cur is None or jac < cur[0]:
            by_param[path] = (jac, f, dp, dr)
    ranked = sorted(by_param.items(), key=lambda kv: kv[1][0])
    lines = ["# Scoring sensitivity (one-at-a-time, x0.5 / x1.5)", "",
             f"Baseline: precision {base_p:.3f}, must-include recall@5 {base_r:.3f}. Lower top-5 Jaccard = rankings move more.", "",
             "| constant | worst top-5 Jaccard | at factor | d precision | d recall |", "|---|---|---|---|---|"]
    lines += [f"| {p} | {j} | x{f} | {dp:+} | {dr:+} |" for p, (j, f, dp, dr) in ranked]
    out = catalog.REPO_ROOT / "reports" / "sensitivity.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
