"""Auction insights: aggregate feedback only, never a rival's bid."""
from pathlib import Path

import pytest

from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.insights import HostInsight, auction_insights

PLANS_DIR = Path(__file__).resolve().parents[1] / "demo_cache" / "plans"


@pytest.fixture(scope="module")
def plans():
    return [CampaignPlan.model_validate_json(p.read_text()) for p in sorted(PLANS_DIR.glob("*.json"))]


def test_insight_invariants(plans):
    plan = next(p for p in plans if p.config.status == "draft" and p.config.budget and p.config.budget.allocations)
    insights = auction_insights(plan, [p for p in plans if p is not plan])
    assert insights
    for i in insights:
        assert 0 <= i.win_rate <= 1
        assert i.avg_bid <= i.max_bid + 1e-9                       # bids never exceed break-even
        if i.avg_price_paid is not None:
            assert i.avg_price_paid <= i.avg_bid + 1e-9           # second price never costs more than the bid
        assert i.verdict in {"winning", "winnable", "not worth chasing"}


def test_no_rival_bid_is_exposed():
    # sealed bids: the report may name rivals and give shares, but has no field carrying their bids
    fields = set(HostInsight.model_fields)
    assert "top_rivals" in fields and not {"rival_bid", "rival_bids", "rival_ecpm"} & fields
    assert HostInsight.model_fields["top_rivals"].annotation == list[tuple[str, float]]


def test_winning_alone_pays_the_reserve_not_the_bid(plans):
    plan = next(p for p in plans if p.config.status == "draft" and p.config.budget and p.config.budget.allocations)
    solo = auction_insights(plan, [])                                # no rivals at all
    assert solo and all(i.win_rate == 1.0 for i in solo)
    assert all(i.avg_price_paid < i.avg_bid for i in solo)           # pays the reserve-based price, not its own bid
