"""Economics, budget (advertiser's cap), objectives, goals and bid strategy (D23): pure code, no LLM."""
from datetime import date

import pytest

from disco_planner.domain.models import AdvertiserInputs
from disco_planner.planning import budget
from disco_planner.planning.publisher_scoring import rank_publishers
from tests.test_publisher_scoring import dog_food_profile

TODAY = date(2026, 10, 30)


def plan_for(inputs: AdvertiserInputs):
    profile = dog_food_profile()
    scores = rank_publishers(profile, TODAY)
    eco = budget.economics(profile, inputs, "pet")
    pricing = budget.pricing_plan(eco, inputs, "pet")
    plan = budget.budget_plan(scores, eco, pricing, inputs, TODAY, None, "pet")
    rows = budget.host_rows(scores, eco, pricing, inputs, 30, "pet")
    return eco, pricing, plan, budget.bid_strategy(pricing, rows, inputs)


def test_no_budget_means_no_allocation_and_no_suggestion():
    _, _, plan, bids = plan_for(AdvertiserInputs())
    assert plan is None                       # we never invent a budget
    assert bids.host_bids                     # but bids per host still exist


def test_no_target_means_no_invented_cpa():
    eco, pricing, *_ = plan_for(AdvertiserInputs(monthly_budget_usd=3000))
    assert eco.target_cpa is None and eco.target_source == "none"
    assert eco.break_even_cac > 0             # shown as guidance only
    assert pricing.cap_source == "category_default"


def test_target_cpa_and_roas_only_when_chosen():
    eco, *_ = plan_for(AdvertiserInputs(bidding="target_cpa", target_cpa_usd=40))
    assert eco.target_cpa == 40
    eco, *_ = plan_for(AdvertiserInputs(bidding="maximize", target_cpa_usd=40))  # given but not chosen
    assert eco.target_cpa is None
    eco, *_ = plan_for(AdvertiserInputs(bidding="target_roas", target_roas=4, price_usd=70))
    assert eco.target_cpa == pytest.approx(70 * eco.expected_orders / 4, rel=0.01)


def test_spend_never_exceeds_budget_and_bids_never_exceed_cap():
    for objective in ("conversions", "clicks", "impressions", "reach"):
        _, pricing, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=2500, objective=objective))
        assert plan.planned_spend_usd <= plan.monthly_budget_usd + 0.01
        assert all(a.budget_usd <= a.capacity_usd + 0.01 for a in plan.allocations)
        assert all(a.start_bid_usd <= a.cpc_bid_usd <= pricing.max_cpc_cap + 1e-9 for a in plan.allocations)


def test_advertiser_max_cpc_is_the_hard_cap():
    _, pricing, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=2500, max_cpc_usd=0.9))
    assert pricing.max_cpc_cap == 0.9 and pricing.cap_source == "advertiser"
    assert all(a.cpc_bid_usd <= 0.9 for a in plan.allocations)


def test_reachable_goal_stops_early_and_saves_budget():
    _, _, small, _ = plan_for(AdvertiserInputs(monthly_budget_usd=50_000, objective="clicks", goal_target=500))
    assert small.goal_status == "reachable" and small.binding_constraint == "goal"
    assert small.planned_spend_usd < small.monthly_budget_usd
    assert small.forecast.mid >= 500 * 0.999


def test_keep_spending_uses_the_budget():
    _, _, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=3000, objective="clicks", goal_target=200,
                                              when_goal_met="continue"))
    assert plan.planned_spend_usd > 0.95 * 3000 or plan.binding_constraint == "inventory"


def test_money_limited_goal_says_how_much_budget_is_needed():
    _, _, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=1500, objective="impressions", goal_target=300_000))
    assert plan.goal_status == "money_limited"
    assert plan.spend_to_reach_goal_usd > 1500
    assert any("Raise the budget" in o for o in plan.replan_options)


def test_inventory_limited_goal_says_money_wont_help():
    _, _, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=1_000_000, objective="impressions",
                                              goal_target=50_000_000))
    assert plan.goal_status == "inventory_limited" and plan.binding_constraint == "inventory"
    assert any("whatever the budget" in o for o in plan.replan_options)


def test_diminishing_returns_spread_the_budget():
    # with a large budget no single host takes everything (water-filling on concave curves)
    _, _, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=20_000, objective="conversions"))
    shares = [a.share for a in plan.allocations]
    assert len(shares) >= 2 and max(shares) < 0.9


def test_bidding_phases_follow_the_mode():
    *_, maximize = plan_for(AdvertiserInputs(monthly_budget_usd=2000))
    assert [p["name"] for p in maximize.phases] == ["learning", "maximize"]
    *_, target = plan_for(AdvertiserInputs(monthly_budget_usd=2000, bidding="target_cpa", target_cpa_usd=60))
    assert [p["name"] for p in target.phases] == ["learning", "enhanced_cpc", "target_cpa"]
    *_, cpm = plan_for(AdvertiserInputs(monthly_budget_usd=2000, objective="reach"))
    assert cpm.current_phase == "fixed_cpm"


def test_relative_fit_is_a_ratio_to_average():
    assert budget.relative_fit(1.0, 1.0) == pytest.approx(1.4)
    assert budget.relative_fit(0.0, 1.0) == pytest.approx(0.6)


def test_no_host_takes_more_than_the_diversification_cap():
    _, _, plan, _ = plan_for(AdvertiserInputs(monthly_budget_usd=5000))
    assert len(plan.allocations) >= 2 and max(a.share for a in plan.allocations) <= 0.5 + 0.01
