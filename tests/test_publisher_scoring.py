"""Scorer tests use hand-built profiles: no LLM, fully deterministic."""
from datetime import date

from disco_planner.domain.models import AdvertiserProfile, NoteSignal, TagRelevance
from disco_planner.planning.publisher_scoring import rank_publishers

TODAY = date(2026, 10, 30)


def dog_food_profile(**overrides) -> AdvertiserProfile:
    base = dict(
        business_summary="Premium senior dog food", product="dog food", is_consumer_product=True,
        price_point_usd=70, price_tier="premium", purchase_model="subscription",
        target_gender="any", relevant_tags=[
            TagRelevance(tag="pet_food", weight=1, why="core"),
            TagRelevance(tag="pet_supplies", weight=0.5, why="adjacent"),
            TagRelevance(tag="subscription", weight=0.25, why="sold by subscription"),
        ],
        competing_tags=["pet_food"],
        note_signals=[NoteSignal(publisher_id="pub_007", alignment=1, why="premium, health-conscious owners")],
        confidence=0.9, gate="proceed", gate_reason="clear",
    )
    return AdvertiserProfile(**(base | overrides))


def by_id(scores):
    return {s.publisher_id: s for s in scores}


def test_pet_hosts_beat_unrelated_hosts():
    scores = by_id(rank_publishers(dog_food_profile(), TODAY))
    # Tailcrate (pet supplies, not a food seller) is the best non-competing host: it anchors the cutoff,
    # so it's always recommended, whatever the competitors score
    assert scores["pub_018"].recommended
    assert not scores["pub_003"].recommended  # Studiogrid: fitness classes
    assert "no category link" in scores["pub_003"].reason


def test_competitor_is_flagged_not_silently_ranked_first():
    scores = by_id(rank_publishers(dog_food_profile(), TODAY))
    assert any("competitor_conflict" in f for f in scores["pub_007"].flags)


def test_generic_tag_alone_does_not_create_a_match():
    # Movewell has "subscription" but nothing to do with dogs.
    scores = by_id(rank_publishers(dog_food_profile(), TODAY))
    assert not scores["pub_002"].recommended


def test_off_network_recommends_nothing():
    profile = dog_food_profile(gate="off_network", gate_reason="B2B software", is_consumer_product=False)
    assert not any(s.recommended for s in rank_publishers(profile, TODAY))


def test_expensive_product_penalised_on_low_aov_host():
    shells = dog_food_profile(
        price_point_usd=650, price_tier="luxury", competing_tags=[], note_signals=[],
        relevant_tags=[TagRelevance(tag="activewear", weight=0.5, why="outdoor-adjacent")],
    )
    scores = by_id(rank_publishers(shells, TODAY))
    price = lambda pid: next(c.value for c in scores[pid].components if c.name == "price_fit")
    assert price("pub_017") > price("pub_002")  # $118 AOV mid-high beats $78 AOV


def test_competitor_is_flagged_but_fit_is_unchanged():
    # Blocking is a host setting enforced at serving (D21); planning must not distort fit.
    with_comp = by_id(rank_publishers(dog_food_profile(), TODAY))["pub_007"]
    without = by_id(rank_publishers(dog_food_profile(competing_tags=[]), TODAY))["pub_007"]
    assert with_comp.score == without.score
    assert any(f.startswith("competitor_conflict") for f in with_comp.flags)


def test_power_mean_family():
    from disco_planner.planning.publisher_scoring import power_mean
    parts = [(0.9, 0.5), (0.1, 0.5)]
    arith, geo, harsh = power_mean(parts, 1), power_mean(parts, 0), power_mean(parts, -5)
    assert abs(arith - 0.5) < 1e-9 and abs(geo - 0.3) < 1e-9
    assert harsh < geo < arith            # lower p = a weak signal counts for more


def test_cutoff_is_anchored_to_best_non_competitor():
    # a competitor's high score must not push non-competing hosts out of the plan
    profile = dog_food_profile()
    scores = rank_publishers(profile, TODAY)
    is_comp = lambda s: any(f.startswith("competitor_conflict") for f in s.flags)
    best_non_comp = next(s for s in scores if not is_comp(s) and s.components[0].value >= 0.2)
    assert best_non_comp.recommended
    assert "below cutoff" not in best_non_comp.reason
