"""Fit the scoring constants that matter from pairwise labels (Bradley-Terry / pairwise logistic).

    log s(host) = a*log(category_fit) + b*log(price_fit) + log M_p(quality components; softmax(w))
    P(A preferred over B) = sigmoid(beta * (log s_A - log s_B))

Fitted: a (relevance exponent), b (price exponent), p (power-mean / compensation), the 5 quality weights,
beta (scale). A Gaussian prior pulls every parameter toward the current hand-set config (MAP estimate):
with a few hundred labels the data can move what it has evidence for and leave the rest. That is also the
production story: today's config is the prior; logged outcomes update it.

Protocol (no leakage): the 100-brand holdout is split (eval/holdout_split.yaml). Fit ONLY on judge pairs from
the 50 DEV brands (cross-validated by advertiser); report on the 50 TEST brands' pairs (never used to fit) and
on the 15 golden examples. Adopt the fit only if it holds on test.

    python -m disco_planner.eval.fit_scoring
"""
from __future__ import annotations

import copy
import json
import math
from datetime import date

import numpy as np
import yaml
from scipy.optimize import minimize

from disco_planner.data import catalog
from disco_planner.eval.pairwise_labels import load_holdout
from disco_planner.eval.sensitivity import evaluate
from disco_planner.llm.provider import default_provider
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
REPORTS = catalog.REPO_ROOT / "reports"
QUALITY = ["audience_fit", "persona_fit", "notes_fit", "scale", "seasonality", "facet_fit"]
# Specification choices, from the grid in reports/fit_scoring.md:
# EPS floors components before log(). At 1e-3 a zero category fit becomes log = -6.9, and the few judge
# answers that prefer a no-link host make the fitter flatten the relevance exponent to escape huge log-loss
# penalties: lower loss, WORSE ranking and golden precision. 0.15 bounds that.
EPS = 0.15
PRIOR_STRENGTH = 50.0  # lambda in sum (theta - theta0)^2; with ~330 labels a weak prior overfits


def components(plan) -> dict[str, np.ndarray]:
    """Per host: [category, price, audience, persona, notes, scale, seasonality] under the current config."""
    out = {}
    for s in rank_publishers(plan.profile, TODAY):
        c = {x.name: (np.nan if x.value is None else x.value) for x in s.components}  # NaN = not stated
        out[s.publisher_id] = np.array([c["category_fit"], c["price_fit"]] + [c[q] for q in QUALITY])
    return out


def theta0() -> np.ndarray:
    cfg = catalog.config("scoring")
    w = np.array([cfg["quality_weights"][q] for q in QUALITY])
    return np.concatenate([[cfg["relevance_exponent"], cfg["price_exponent"], cfg["quality_p"], 5.0], np.log(w)])


def log_score(theta: np.ndarray, X: np.ndarray) -> np.ndarray:
    a, b, p = theta[0], theta[1], theta[2]
    w = np.exp(theta[4:]) / np.exp(theta[4:]).sum()
    known = ~np.isnan(X)
    X = np.maximum(np.nan_to_num(X, nan=1.0), EPS)
    q, qk = X[:, 2:], known[:, 2:]
    wk = w * qk
    wk = wk / np.maximum(wk.sum(axis=1, keepdims=True), 1e-12)   # renormalise over known components (D25)
    if abs(p) < 1e-3:
        log_m = (np.log(q) * wk).sum(axis=1)
    else:
        log_m = np.log(np.maximum((wk * q**p).sum(axis=1), 1e-12)) / p
    log_price = np.where(known[:, 1], np.log(X[:, 1]), 0.0)          # unknown price: no information
    return a * np.log(X[:, 0]) + b * log_price + log_m


def nll(theta, XA, XB, y, th0, fit_mask):
    d = theta[3] * (log_score(theta, XA) - log_score(theta, XB))
    prob = 1 / (1 + np.exp(-np.clip(d, -30, 30)))
    loss = -np.mean(y * np.log(prob + 1e-12) + (1 - y) * np.log(1 - prob + 1e-12))
    prior = PRIOR_STRENGTH / len(y) * np.sum(((theta - th0) * fit_mask) ** 2)
    return loss + prior


BOUNDS = [(0.1, 3.0), (0.0, 2.0), (-3.0, 3.0), (0.05, 50.0)] + [(-5.0, 5.0)] * len(QUALITY)


def fit(XA, XB, y, th0, only_beta: bool = False) -> np.ndarray:
    mask = np.ones_like(th0)
    mask[3] = 0  # no prior on beta (pure scale)
    if only_beta:  # hand-set config, only the scale fitted: the fair baseline
        res = minimize(lambda b: nll(np.concatenate([th0[:3], b, th0[4:]]), XA, XB, y, th0, mask),
                       x0=[th0[3]], bounds=[BOUNDS[3]], method="L-BFGS-B")
        return np.concatenate([th0[:3], res.x, th0[4:]])
    res = minimize(nll, x0=th0, args=(XA, XB, y, th0, mask), bounds=BOUNDS, method="L-BFGS-B")
    return res.x


def metrics(theta, XA, XB, y) -> dict:
    d = log_score(theta, XA) - log_score(theta, XB)
    prob = 1 / (1 + np.exp(-np.clip(theta[3] * d, -30, 30)))
    return {"accuracy": float(np.mean((d > 0) == (y == 1))),
            "log_loss": float(-np.mean(y * np.log(prob + 1e-12) + (1 - y) * np.log(1 - prob + 1e-12)))}


def to_config(theta: np.ndarray) -> dict:
    w = np.exp(theta[4:]) / np.exp(theta[4:]).sum()
    return {"relevance_exponent": round(float(theta[0]), 3), "price_exponent": round(float(theta[1]), 3),
            "quality_p": round(float(theta[2]), 3), "quality_weights": {q: round(float(v), 3) for q, v in zip(QUALITY, w)}}


def golden_check(params: dict) -> dict:
    golden = {c["id"]: c for c in yaml.safe_load((catalog.REPO_ROOT / "src/disco_planner/eval/golden.yaml").read_text())}
    provider = default_provider()
    profiles = {cid: parse_brief(c["text"], provider, TODAY) for cid, c in golden.items()}
    cfg = catalog.config("scoring")
    saved = copy.deepcopy(cfg)
    base = evaluate(profiles, golden, None)
    try:
        cfg.update(params)
        new = evaluate(profiles, golden, base["recs"])
    finally:
        cfg.clear()
        cfg.update(saved)
    return {"hand_set": {"precision": round(base["precision"], 3), "recall": round(base["recall"], 3)},
            "fitted": {"precision": round(new["precision"], 3), "recall": round(new["recall"], 3),
                       "top5_change_vs_hand_set": round(new["change"], 3)}}


def main() -> None:
    labels = [json.loads(line) for line in (REPORTS / "pairwise_labels.jsonl").read_text().splitlines()]
    labels = [r for r in labels if r.get("label") in ("A", "B")]
    plans = {adv: plan for adv, _, plan in load_holdout()}
    comps = {adv: components(plans[adv]) for adv in {r["advertiser"] for r in labels} if adv in plans}
    labels = [r for r in labels if r["advertiser"] in comps]
    XA = np.array([comps[r["advertiser"]][r["a"]] for r in labels])
    XB = np.array([comps[r["advertiser"]][r["b"]] for r in labels])
    y = np.array([1.0 if r["label"] == "A" else 0.0 for r in labels])
    split = yaml.safe_load((catalog.REPO_ROOT / "src/disco_planner/eval/holdout_split.yaml").read_text())
    is_dev = np.array([r["advertiser"] in set(split["dev"]) for r in labels])
    XA_test, XB_test, y_test = XA[~is_dev], XB[~is_dev], y[~is_dev]
    XA, XB, y = XA[is_dev], XB[is_dev], y[is_dev]
    groups = np.array([r["advertiser"] for r in labels])[is_dev]
    th0 = theta0()

    # 5-fold cross-validation grouped by advertiser
    advs = sorted(set(groups))
    rng = np.random.default_rng(3)
    rng.shuffle(advs)
    folds = [set(advs[i::5]) for i in range(5)]
    cv = {"hand_set": [], "fitted": []}
    for fold in folds:
        test = np.array([g in fold for g in groups])
        tr = ~test
        for name, only_beta in (("hand_set", True), ("fitted", False)):
            th = fit(XA[tr], XB[tr], y[tr], th0, only_beta=only_beta)
            cv[name].append(metrics(th, XA[test], XB[test], y[test]))
    cv_mean = {k: {m: round(float(np.mean([f[m] for f in v])), 3) for m in ("accuracy", "log_loss")} for k, v in cv.items()}

    th_all = fit(XA, XB, y, th0)
    th_hand = fit(XA, XB, y, th0, only_beta=True)
    test = {"hand_set": metrics(th_hand, XA_test, XB_test, y_test), "fitted": metrics(th_all, XA_test, XB_test, y_test)}
    fitted = to_config(th_all)
    hand = to_config(th0)
    golden = golden_check(fitted)

    lines = [
        "# Fitting the scoring constants from pairwise labels", "",
        f"Labels: order-consistent, decisive LLM-judge pairs (prompts/pairwise_judge.md, both orders asked). "
        f"Fit on {len(y)} DEV pairs; {len(y_test)} TEST pairs never used to fit.", "",
        "## TEST brands (never used to fit): pairwise agreement with the judge", "",
        "| model | accuracy | log loss |", "|---|---|---|",
        f"| hand-set config | {test['hand_set']['accuracy']:.3f} | {test['hand_set']['log_loss']:.3f} |",
        f"| fitted on dev | {test['fitted']['accuracy']:.3f} | {test['fitted']['log_loss']:.3f} |", "",
        "## DEV brands: 5-fold CV by advertiser", "",
        "| model | accuracy | log loss |", "|---|---|---|",
        f"| hand-set config (scale only fitted) | {cv_mean['hand_set']['accuracy']} | {cv_mean['hand_set']['log_loss']} |",
        f"| fitted (MAP, prior = hand-set) | {cv_mean['fitted']['accuracy']} | {cv_mean['fitted']['log_loss']} |", "",
        "## Parameters", "", "| parameter | hand-set | fitted |", "|---|---|---|",
        f"| relevance_exponent | {hand['relevance_exponent']} | {fitted['relevance_exponent']} |",
        f"| price_exponent | {hand['price_exponent']} | {fitted['price_exponent']} |",
        f"| quality_p | {hand['quality_p']} | {fitted['quality_p']} |",
    ] + [f"| weight: {q} | {hand['quality_weights'][q]} | {fitted['quality_weights'][q]} |" for q in QUALITY] + [
        "", "## Independent check on the 15 golden examples (never used for fitting)", "",
        "| | precision | must-include recall@5 |", "|---|---|---|",
        f"| hand-set | {golden['hand_set']['precision']} | {golden['hand_set']['recall']} |",
        f"| fitted | {golden['fitted']['precision']} | {golden['fitted']['recall']} |",
        f"\nTop-5 change vs hand-set on golden: {golden['fitted']['top5_change_vs_hand_set']}",
    ]
    (REPORTS / "fit_scoring.md").write_text("\n".join(lines) + "\n")
    (REPORTS / "fitted_params.yaml").write_text(yaml.safe_dump({"fitted": fitted, "hand_set": hand, "cv_dev": cv_mean,
                                                                 "test": test, "golden": golden}))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
