"""Market-based planning (D24): budget is the advertiser's cap, prices come from the simulated auction."""
import itertools
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


def test_greedy_allocation_matches_brute_force():
    rng = random.Random(1)
    for _ in range(30):
        markets = []
        for h in range(3):
            needed = sorted(rng.uniform(0.2, 3.0) for _ in range(5))
            markets.append(dict(score=SimpleNamespace(publisher_id=f"h{h}"), needed=needed, k_max=5,
                                unit_per_view=rng.uniform(0.01, 0.05), bucket=100.0, ctr=0.03))
        budget_usd = rng.uniform(5, 40)
        greedy = budget._summary(markets, budget.allocate(markets, budget_usd, None, 1.0))[1]
        best = 0.0
        for ks in itertools.product(range(6), repeat=3):
            taken = {f"h{i}": k for i, k in enumerate(ks)}
            spend, units = budget._summary(markets, taken)
            if spend <= budget_usd + 1e-9:
                best = max(best, units)
        # greedy is optimal up to the last indivisible bucket (a knapsack edge); allow one bucket of slack
        slack = max(m["unit_per_view"] * m["bucket"] for m in markets)
        assert greedy >= best - slack - 1e-9


def test_quantile():
    assert market.quantile([1, 2, 3, 4, 5], 0.5) == 3 and market.quantile([], 0.5) == 0.0


def test_cap_that_strands_budget_is_called_out():
    _, _, plan, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=50_000, max_share_per_host=0.34, aggressiveness="conservative"))
    if plan.planned_spend_usd < 50_000 - 1 and plan.binding_constraint == "budget":
        assert any("leaves" in o and "unspent" in o for o in plan.replan_options)
