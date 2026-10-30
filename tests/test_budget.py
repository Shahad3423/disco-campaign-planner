"""Economics, delivery goals and bid strategy: pure code, no LLM."""
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
    return eco, pricing, plan, budget.bid_strategy(pricing, plan)


def test_target_cpa_wins_and_margin_not_needed():
    eco, *_ = plan_for(AdvertiserInputs(target_cpa_usd=40, gross_margin=0.5))
    assert eco.target_cpa == 40 and eco.cpa_source == "target_cpa" and eco.gross_margin is None


def test_target_roas_sets_cpa_from_revenue():
    eco, *_ = plan_for(AdvertiserInputs(target_roas=4, price_usd=70))
    assert eco.cpa_source == "target_roas"
    assert eco.target_cpa == pytest.approx(70 * eco.expected_orders / 4, rel=0.01)


def test_margin_is_last_resort_and_labelled():
    eco, *_ = plan_for(AdvertiserInputs())
    assert eco.cpa_source == "margin" and eco.margin_source == "category_default"
    assert any("margin" in a for a in eco.assumptions)


def test_impression_goal_turns_into_budget_and_hits_target():
    _, pricing, plan, _ = plan_for(AdvertiserInputs(delivery_goal="impressions", target_impressions=200_000))
    assert pricing.model == "CPM" and plan.budget_source == "impression_goal"
    assert plan.expected_impressions == pytest.approx(200_000, rel=0.02)
    assert plan.goal_feasible


def test_infeasible_reach_goal_is_flagged():
    *_, plan, _ = plan_for(AdvertiserInputs(delivery_goal="reach", target_reach=50_000_000))
    assert not plan.goal_feasible and any("can deliver" in w for w in plan.warnings)


def test_bid_strategy_starts_in_learning_below_ceiling():
    *_, plan, bids = plan_for(AdvertiserInputs())
    assert bids.current_phase == "learning"
    assert all(h.floor <= h.start_bid <= h.max_bid for h in bids.host_bids)
    assert [p["name"] for p in bids.phases] == ["learning", "enhanced_cpc", "target_cpa"]


def test_relative_fit_is_a_ratio_to_average():
    assert budget.relative_fit(1.0, 1.0) == pytest.approx(1.4)
    assert budget.relative_fit(0.0, 1.0) == pytest.approx(0.6)
