"""Market-based planning (D24): budget is the advertiser's cap, prices come from the simulated auction."""
import math
import random
from datetime import date
from types import SimpleNamespace

import pytest

from disco_planner.domain.models import AdvertiserInputs
from disco_planner.planning import budget, market
from disco_planner.planning.publisher_scoring import rank_publishers
from tests.test_publisher_scoring import dog_food_profile

TODAY = date(2026, 10, 30)


def plan_for(inputs: AdvertiserInputs):
    profile = dog_food_profile()
    scores = rank_publishers(profile, TODAY)
    eco = budget.economics(profile, inputs, "pet")
    markets = budget.host_markets(scores, "test brief", eco, inputs, 30, "pet")
    pricing = budget.pricing_plan(eco, inputs, markets)
    plan = budget.budget_plan(markets, inputs, TODAY, None)
    return eco, pricing, plan, budget.bid_strategy(pricing, markets, inputs, plan), markets


def test_no_budget_means_no_allocation_but_bids_exist():
    _, _, plan, bids, _ = plan_for(AdvertiserInputs())
    assert plan is None and bids.host_bids


def test_no_target_is_invented():
    eco, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=3000))
    assert eco.target_cpa is None and eco.target_source == "none"
    eco, *_ = plan_for(AdvertiserInputs(bidding="maximize", target_cpa_usd=40))  # given but not chosen
    assert eco.target_cpa is None
    eco, *_ = plan_for(AdvertiserInputs(bidding="target_roas", target_roas=4, price_usd=70))
    assert eco.target_cpa == pytest.approx(70 * eco.expected_orders / 4, rel=0.01)


def test_landscape_is_sorted_and_win_share_rises_with_bid():
    _, _, _, _, markets = plan_for(AdvertiserInputs(monthly_budget_usd=3000))
    for m in markets:
        n = m["needed"]
        assert n == sorted(n)
        wins = [sum(x <= b for x in n) for b in (n[len(n) // 4], n[len(n) // 2], n[-1])]
        assert wins == sorted(wins)


@pytest.mark.parametrize("objective", ["conversions", "clicks", "impressions", "reach"])
def test_invariants_spend_bids_and_second_price(objective):
    _, _, plan, _, _ = plan_for(AdvertiserInputs(monthly_budget_usd=2500, objective=objective))
    assert plan.planned_spend_usd <= plan.monthly_budget_usd + 0.01
    for a in plan.allocations:
        assert a.budget_usd <= plan.max_share_per_host * plan.monthly_budget_usd + 0.01  # default 1.0 = off
        if a.budget_usd:
            assert a.avg_price_paid <= a.cpc_bid_usd + 1e-9          # second price never exceeds the bid
            assert 0 < a.win_share <= 0.6 + 1e-9                     # balanced = up to 60% of page views


def test_advertiser_max_cpc_is_never_exceeded():
    _, _, plan, bids, _ = plan_for(AdvertiserInputs(monthly_budget_usd=2500, max_cpc_usd=0.8))
    assert all(a.cpc_bid_usd <= 0.8 for a in plan.allocations if a.budget_usd)
    assert all(h.max_bid <= 0.8 for h in bids.host_bids)


def test_more_aggressive_wins_more_and_pays_more_at_the_margin_per_host():
    # The law is per host: winning a larger share of the SAME host needs a higher bid. The average across hosts
    # can even fall (aggressive can go deep on cheap hosts instead of being forced onto an expensive one).
    plans = {}
    for agg in ("conservative", "aggressive"):
        _, _, plan, _, _ = plan_for(AdvertiserInputs(monthly_budget_usd=1_000_000, aggressiveness=agg, objective="clicks"))
        plans[agg] = plan
    assert plans["aggressive"].forecast.mid > plans["conservative"].forecast.mid
    cons = {a.publisher_id: a for a in plans["conservative"].allocations}
    for a in plans["aggressive"].allocations:
        c = cons[a.publisher_id]
        if a.win_share > c.win_share:
            assert a.cpc_bid_usd >= c.cpc_bid_usd


def test_goal_status_reachable_money_and_inventory_limited():
    _, _, ok, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=50_000, objective="clicks", goal_target=300))
    assert ok.goal_status == "reachable" and ok.planned_spend_usd < 50_000
    _, _, money, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=300, objective="clicks", goal_target=5000))
    assert money.goal_status == "money_limited" and money.spend_to_reach_goal_usd > 300
    _, _, inv, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=10_000_000, objective="impressions", goal_target=10**9))
    assert inv.goal_status == "inventory_limited" and any("whatever the budget" in o for o in inv.replan_options)


def test_share_cap_is_respected_and_its_cost_reported():
    _, _, plan, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=20_000, max_share_per_host=0.4))
    assert all(a.budget_usd <= 0.4 * plan.monthly_budget_usd + 0.01 for a in plan.allocations)  # cap is on the BUDGET
    assert plan.forecast_without_share_cap >= plan.forecast.mid - 1e-6


def _random_markets(rng, hosts=3, buckets=6):
    markets = []
    for h in range(hosts):
        needed = sorted(rng.uniform(0.2, 3.0) for _ in range(buckets))
        markets.append(dict(score=SimpleNamespace(publisher_id=f"h{h}"), needed=needed, k_max=rng.randint(1, buckets),
                            unit_per_view=rng.uniform(0.01, 0.05), bucket=rng.choice([50.0, 100.0, 1500.0]), ctr=0.03))
    return markets


def _lp_best_units(markets, budget_usd, share_cap):
    """Exact optimum of the allocation LP: x[h,k] in [0,1] = share of bucket k of host h bought."""
    linprog = pytest.importorskip("scipy.optimize").linprog
    idx = [(h, k) for h, m in enumerate(markets) for k in range(m["k_max"])]
    cost = [budget._bucket_cost(markets[h], k) for h, k in idx]
    gain = [markets[h]["unit_per_view"] * markets[h]["bucket"] for h, _ in idx]
    rows = [cost] + [[c if hh == h else 0.0 for (hh, _), c in zip(idx, cost)] for h in range(len(markets))]
    caps = [budget_usd] + [share_cap * budget_usd] * len(markets)
    res = linprog([-g for g in gain], A_ub=rows, b_ub=caps, bounds=[(0, 1)] * len(idx), method="highs")
    return -res.fun


def test_allocation_equals_the_lp_optimum():
    # The allocation is a linear program (partial buckets allowed); cheapest-first must hit its exact optimum,
    # with and without a per-host share cap. No slack: the old whole-bucket version needed one bucket of it.
    rng = random.Random(1)
    for _ in range(200):
        markets = _random_markets(rng)
        budget_usd, cap = rng.uniform(5, 400), rng.choice([1.0, 0.5, 0.4])
        ours = budget._summary(markets, budget.allocate(markets, budget_usd, None, cap))[1]
        assert ours == pytest.approx(_lp_best_units(markets, budget_usd, cap), rel=1e-6, abs=1e-9)


def test_allocation_optimality_certificate():
    # Without scipy: with only the budget binding, every bucket bought must be at least as cheap per unit as every
    # bucket left on the table (an LP's optimality condition), and the budget is spent unless inventory runs out.
    rng = random.Random(2)
    for _ in range(200):
        markets = _random_markets(rng)
        budget_usd = rng.uniform(5, 400)
        taken = budget.allocate(markets, budget_usd, None, 1.0)
        bought, left = [], []
        for m in markets:
            t = taken[m["score"].publisher_id]
            for k in range(m["k_max"]):
                ratio = budget._bucket_cost(m, k) / (m["unit_per_view"] * m["bucket"])  # cost per unit
                (bought if k < t - 1e-9 else left).append(ratio)
        if bought and left:
            assert max(bought) <= min(left) + 1e-9
        spend = budget._summary(markets, taken)[0]
        assert spend == pytest.approx(budget_usd, rel=1e-9) or not left


def test_goal_is_met_exactly_at_the_lowest_cost():
    rng = random.Random(3)
    for _ in range(100):
        markets = _random_markets(rng)
        full = budget._summary(markets, {m["score"].publisher_id: float(m["k_max"]) for m in markets})[1]
        goal = rng.uniform(0.1, 0.9) * full
        spend, units = budget._summary(markets, budget.allocate(markets, math.inf, goal, 1.0))
        assert units == pytest.approx(goal, rel=1e-9)
        cheapest = sorted((budget._bucket_cost(m, k) / (m["unit_per_view"] * m["bucket"]), m["unit_per_view"] * m["bucket"])
                          for m in markets for k in range(m["k_max"]))
        need, best = goal, 0.0
        for per_unit, units_in_bucket in cheapest:  # buy the cheapest units until the goal: the minimum spend
            take = min(need, units_in_bucket)
            best, need = best + take * per_unit, need - take
            if need <= 0:
                break
        assert spend == pytest.approx(best, rel=1e-9)


def test_whole_budget_is_spent_and_the_split_moves_smoothly():
    # The whole-bucket version left $30-400 unspent and moved ~$2,000 between hosts when the budget rose by $250.
    prev = None
    for b in range(2000, 8001, 250):
        _, _, plan, _, _ = plan_for(AdvertiserInputs(monthly_budget_usd=b))
        assert plan.planned_spend_usd == pytest.approx(b, abs=0.05)
        spend = {a.publisher_id: a.budget_usd for a in plan.allocations}
        if prev:
            assert plan.forecast.mid >= prev[0].forecast.mid
            assert all(abs(spend[h] - prev[1][h]) <= 250 + 0.05 for h in spend)  # no host moves more than the budget did
        prev = (plan, spend)


def test_quantile():
    assert market.quantile([1, 2, 3, 4, 5], 0.5) == 3 and market.quantile([], 0.5) == 0.0


def test_cap_that_strands_budget_is_called_out():
    _, _, plan, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=50_000, max_share_per_host=0.34, aggressiveness="conservative"))
    if plan.planned_spend_usd < 50_000 - 1 and plan.binding_constraint == "budget":
        assert any("leaves" in o and "unspent" in o for o in plan.replan_options)


def test_exploration_gets_a_small_separate_test_budget_and_never_touches_the_goal_math():
    from disco_planner.data import catalog
    _, _, plan, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=10_000))
    share = catalog.config("scoring")["exploration"]["budget_share"]
    explore = [a for a in plan.allocations if a.exploration]
    main = [a for a in plan.allocations if not a.exploration]
    assert explore and main
    assert sum(a.budget_usd for a in explore) <= share * 10_000 + 0.01          # a small slice
    assert plan.planned_spend_usd <= plan.monthly_budget_usd + 0.01
    assert all("EXPLORATION TEST" in a.rationale for a in explore)


def test_no_exploration_budget_when_stopping_at_a_goal():
    _, _, plan, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=50_000, objective="clicks", goal_target=300))
    assert all(a.budget_usd == 0 for a in plan.allocations if a.exploration)


def test_bid_for_a_partial_bucket_lies_between_its_price_levels():
    m = dict(needed=[0.5, 0.6, 1.0, 1.4])
    assert budget._bid_for(m, 1.0) == 0.5 and budget._bid_for(m, 3.0) == 1.0 and budget._bid_for(m, 0.3) == 0.5
    assert budget._bid_for(m, 2.25) == pytest.approx(0.6 + 0.25 * 0.4)
