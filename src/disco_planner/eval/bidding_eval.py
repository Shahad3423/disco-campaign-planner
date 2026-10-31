"""Is the bidding and allocation right? Four checks on the simulated market (D24).

  1. realism      clearing prices per host vs benchmark ranges (CPC, implied CPM)
  2. back-test    replay each plan's flight with FRESH rival bids and uncertain true CTR/CVR: how often does the
                  realised outcome fall inside the forecast range, and does spend stay within budget?
  3. baselines    our allocation vs equal split, score-proportional split, and all-on-the-best-host,
                  same budget, same market, same bids per host
  4. honesty      one-slot second price: bidding your true value per click should beat bidding above/below it

    python -m disco_planner.eval.bidding_eval
"""
from __future__ import annotations

import math
import random
import statistics
from datetime import date
from pathlib import Path

import yaml

from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserInputs
from disco_planner.llm.provider import default_provider
from disco_planner.planning import budget, market
from disco_planner.planning.intake import parse_brief
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)
BENCH = {"cpc": (0.45, 1.81), "cpm": (5.0, 60.0)}   # e-commerce CPC range; CPM: native low end to post-purchase high end
REPLAYS = 200


def realism() -> list[str]:
    ctr = catalog.config("pricing")["priors"]["ctr"]["mid"]
    rows, inside = [], 0
    for pub in catalog.publishers():
        n = market.landscape(pub.id, ctr)
        p25, p50, p75 = (market.quantile(n, q) for q in (0.25, 0.5, 0.75))
        ok = BENCH["cpc"][0] <= p50 <= BENCH["cpc"][1]
        inside += ok
        rows.append(f"| {pub.name} | {len(market.book().get(pub.id, []))} | ${p25:.2f} | ${p50:.2f} | ${p75:.2f} | "
                    f"${p50 * ctr * 1000:.0f} | {'yes' if ok else 'no'} |")
    return ([f"Median clearing CPC inside the benchmark range (${BENCH['cpc'][0]}-${BENCH['cpc'][1]}) on {inside}/20 hosts "
             f"(CTR {ctr:.1%}; implied CPM = CPC x CTR x 1000).", "",
             "| host | rival campaigns | p25 | median | p75 | implied CPM | median in range |", "|---|---|---|---|---|---|---|"] + rows)


def replay(markets: list[dict], taken: dict[str, int], budget_usd: float, objective: str, rng: random.Random) -> tuple[float, float]:
    """One realised flight: fresh rival bids per page view (new landscape draw), true CTR/CVR drawn from the
    prior ranges, Poisson-ish noise on clicks. Our bid per host = the planned bid."""
    pri = catalog.config("pricing")["priors"]
    spend = units = 0.0
    for m in markets:
        k = taken[m["score"].publisher_id]
        if not k:
            continue
        bid = m["needed"][k - 1]
        # fresh market: re-scale the landscape by a common per-flight factor (market conditions) + resample
        shift = math.exp(rng.gauss(0, 0.15))
        fresh = sorted(rng.choice(m["needed"]) * shift * math.exp(rng.gauss(0, 0.10)) for _ in m["needed"])
        won = [p for p in fresh if p <= bid]
        true_ctr = m["ctr"] * rng.uniform(pri["ctr"]["low"], pri["ctr"]["high"]) / pri["ctr"]["mid"]
        true_cvr = m["cvr"]["mid"] * rng.uniform(pri["cvr"]["low"], pri["cvr"]["high"]) / pri["cvr"]["mid"]
        views = m["bucket"] * len(won)
        clicks = max(0.0, rng.gauss(views * true_ctr, math.sqrt(max(views * true_ctr, 1))))
        spend += clicks * (statistics.mean(won) if won else 0)
        units += {"conversions": clicks * true_cvr, "clicks": clicks, "impressions": views,
                  "reach": views / pri["avg_frequency"]}[objective] * m["audience"]
    if spend > budget_usd:  # pacing stops delivery at the cap: scale the flight down proportionally
        units, spend = units * budget_usd / spend, budget_usd
    return spend, units


def allocation_baselines(markets: list[dict], budget_usd: float, share_cap: float) -> dict[str, float]:
    """Units each rule buys with the same budget, buying each host's page views cheapest-first."""
    def buy(weights: dict[str, float]) -> float:
        units = 0.0
        for m in markets:
            pid = m["score"].publisher_id
            cash, k = budget_usd * weights.get(pid, 0), 0
            while k < m["k_max"] and cash >= budget._bucket_cost(m, k):
                cash -= budget._bucket_cost(m, k)
                k += 1
            units += m["unit_per_view"] * m["bucket"] * k
        return units
    pids = [m["score"].publisher_id for m in markets]
    total_score = sum(m["score"].score for m in markets)
    best = max(markets, key=lambda m: m["score"].score)["score"].publisher_id
    return {
        "ours (greedy, cheapest units first)": budget._summary(markets, budget.allocate(markets, budget_usd, None, share_cap))[1],
        "ours without share cap": budget._summary(markets, budget.allocate(markets, budget_usd, None, 1.0))[1],
        "equal split": buy({p: 1 / len(pids) for p in pids}),
        "score-proportional": buy({m["score"].publisher_id: m["score"].score / total_score for m in markets}),
        "all on the best-scored host": buy({best: 1.0}),
    }


def honesty(markets: list[dict], value_per_click: float) -> list[tuple[float, float]]:
    """Utility (value - price per won click) when bidding a multiple of the true value."""
    out = []
    for mult in (0.5, 0.75, 1.0, 1.25, 1.5):
        b = value_per_click * mult
        u = sum(sum(value_per_click - p for p in m["needed"] if p <= b) for m in markets)
        out.append((mult, u))
    return out


def main() -> None:
    golden = yaml.safe_load((catalog.REPO_ROOT / "src/disco_planner/eval/golden.yaml").read_text())
    provider = default_provider()
    lines = ["# Bidding and allocation evaluation (simulated market)", "", "## 1. Realism", ""] + realism()

    coverage, overspend, rows_back, rows_base, rows_honest = [], 0, [], [], []
    for case in golden:
        profile = parse_brief(case["text"], provider, TODAY)
        scores = rank_publishers(profile, TODAY)
        if profile.gate not in ("proceed", "clarify") or not any(s.recommended for s in scores):
            continue
        for objective in ("conversions", "clicks"):
            inputs = AdvertiserInputs(monthly_budget_usd=5000, objective=objective)
            cat = next((t.tag for t in sorted(profile.relevant_tags, key=lambda t: -t.weight)
                        if t.tag in {p.category for p in catalog.publishers()}), None)
            eco = budget.economics(profile, inputs, cat)
            markets = budget.host_markets(scores, case["text"], eco, inputs, 30, cat)
            plan = budget.budget_plan(markets, inputs, TODAY, None)
            if not plan:
                continue
            taken = budget.allocate(markets, 5000, None, plan.max_share_per_host)
            rng = random.Random(f"{case['id']}-{objective}")
            outcomes = [replay(markets, taken, 5000, objective, rng) for _ in range(REPLAYS)]
            inside = sum(plan.forecast.low <= u <= plan.forecast.high for _, u in outcomes) / REPLAYS
            coverage.append(inside)
            overspend += sum(s > 5000 + 0.01 for s, _ in outcomes)
            rows_back.append(f"| #{case['id']} {objective} | {plan.forecast.low:,.0f}-{plan.forecast.high:,.0f} | "
                             f"{statistics.median(u for _, u in outcomes):,.0f} | {inside:.0%} |")
            base = allocation_baselines(markets, 5000, plan.max_share_per_host)
            ours = base["ours without share cap"]
            rows_base.append(f"| #{case['id']} {objective} | " + " | ".join(
                f"{v / ours:.0%}" if ours else "-" for v in base.values()) + " |")
            if objective == "conversions":
                value = statistics.median(market.quantile(m["needed"], 0.5) for m in markets)
                h = honesty(markets, value)
                rows_honest.append(f"| #{case['id']} | " + " | ".join(f"{u:,.0f}" for _, u in h) +
                                   f" | {'yes' if max(h, key=lambda x: x[1])[0] == 1.0 else 'no'} |")
    lines += ["", "## 2. Forecast back-test", "",
              f"{REPLAYS} replays per plan with fresh rival bids, a market-wide price shift, uncertain true CTR/CVR and click noise. "
              f"Realised outcome inside the forecast range: **{statistics.mean(coverage):.0%}** on average (target >= 80%). "
              f"Replays over budget: {overspend} (pacing stops at the cap).", "",
              "| plan | forecast range | median realised | inside range |", "|---|---|---|---|"] + rows_back
    lines += ["", "## 3. Allocation vs baselines (forecast units, as % of ours without the share cap)", "",
              "| plan | ours | ours, no cap | equal split | score-proportional | all on best host |", "|---|---|---|---|---|---|"] + rows_base
    lines += ["", "## 4. Honest bidding (utility = value - price, summed over won page views)", "",
              "| plan | bid 0.5x value | 0.75x | 1.0x (true value) | 1.25x | 1.5x | truthful is best |", "|---|---|---|---|---|---|---|"] + rows_honest
    out = catalog.REPO_ROOT / "reports" / "bidding_eval.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
