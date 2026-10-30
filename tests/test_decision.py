"""Serving decision engine: runs on cached demo plans, no LLM."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.decision import persona_posterior, serve
from disco_planner.serving.features import ShopperSignals, build_features

NOW = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
PLANS_DIR = Path(__file__).resolve().parents[1] / "demo_cache" / "plans"


@pytest.fixture(scope="module")
def plans():
    return [CampaignPlan.model_validate_json(p.read_text()) for p in sorted(PLANS_DIR.glob("*.json"))]


def test_posterior_is_a_distribution():
    f = build_features(ShopperSignals(publisher_id="pub_009", zip="10001", first_name="Sarah"), NOW)
    post, _ = persona_posterior(f, "pub_009")
    assert abs(sum(post.values()) - 1) < 0.01


def test_junk_name_never_inserted(plans):
    d = serve(ShopperSignals(publisher_id="pub_017", zip="80435", first_name="asdf"), NOW, plans)
    assert all("asdf" not in o.headline.lower() and "{first_name}" not in o.headline for o in d.offers)


def test_offers_only_from_campaigns_allocated_to_this_host(plans):
    d = serve(ShopperSignals(publisher_id="pub_011", zip="10001", first_name="Emily"), NOW, plans)
    for o in d.offers:
        plan = next(p for p in plans if (p.profile.product or p.brief[:40]) == o.campaign)
        assert any(a.publisher_id == "pub_011" for a in plan.config.budget.allocations)


def test_offers_sorted_by_ecpm(plans):
    d = serve(ShopperSignals(publisher_id="pub_017", zip="80435", first_name="Jordan"), NOW, plans)
    assert [o.ecpm for o in d.offers] == sorted((o.ecpm for o in d.offers), reverse=True)
