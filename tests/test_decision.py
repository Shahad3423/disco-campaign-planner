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


def test_final_bid_never_exceeds_break_even(plans):
    sig = ShopperSignals(publisher_id="pub_017", zip="80435", first_name="Jordan")
    for phase in ("learning", "enhanced_cpc"):
        for pacing in (0.3, 1.0, 3.0):
            d = serve(sig, NOW, plans, phase=phase, pacing_ratio=pacing)
            for o in d.offers:
                plan = next(p for p in plans if (p.profile.product or p.brief[:40]) == o.campaign)
                if o.pricing_model == "CPC":
                    alloc = next(a for a in plan.config.budget.allocations if a.publisher_id == "pub_017")
                    assert o.bid <= alloc.cpc_bid_usd + 1e-9


def test_learning_phase_ignores_conversion_likelihood(plans):
    sig = ShopperSignals(publisher_id="pub_017", zip="80435", first_name="Michael")
    d = serve(sig, NOW, plans, phase="learning")
    assert all("conversion likelihood 1.00" in o.bid_breakdown for o in d.offers if o.pricing_model == "CPC")


def _offer(ecpm, bid, ctr=0.01, model="CPC"):
    from disco_planner.serving.decision import Offer
    return Offer(campaign=f"c{ecpm}", publisher_id="pub_001", persona_id="persona_001", headline="h", body="b",
                 cta="c", pricing_model=model, bid=bid, expected_ctr=ctr, ecpm=ecpm, reasons=[])


def test_second_price_never_exceeds_bid_and_is_ordered():
    from disco_planner.data import catalog
    from disco_planner.serving.decision import run_auction
    cfg = catalog.config("serving")["auction"]
    offers = [_offer(40, 4.0), _offer(30, 3.0), _offer(20, 2.0), _offer(10, 1.0), _offer(0.5, 0.05)]
    winners, notes = run_auction(offers, cfg)
    assert [w.slot for w in winners] == [1, 2, 3]
    assert all(w.price_paid <= w.bid for w in winners)
    # slot 1 pays what it takes to beat slot 2: 30 / (0.01 x 1000) + 0.01 = 3.01
    assert abs(winners[0].price_paid - 3.01) < 1e-6
    assert any("reserve" in n for n in notes) and any("outbid" in n for n in notes)
