"""Creative stage rules (D35): the fact sheet only holds what the brief states; failed lines never ship silently."""
import json

from disco_planner.domain.models import AdvertiserProfile, PersonaMatch
from disco_planner.llm.provider import FakeProvider
from disco_planner.planning.creative import generate_creatives, offer_facts


def profile(**kw):
    base = dict(business_summary="dog food", product="dog food", is_consumer_product=True, gate="proceed", gate_reason="x",
                stated_claims=["Grain-free"])
    return AdvertiserProfile(**{**base, **kw})


def test_offer_terms_come_from_the_brief_only():
    p = profile(purchase_model="subscription", fulfillment_notes="Ships in 6 weeks.", price_point_usd=40)
    assert offer_facts(p, "Grain-free dog food, $40, monthly subscription.") == [
        "Grain-free", "Sold as a subscription", "Ships in 6 weeks.", "Price: about $40"]
    # an inferred purchase model or price is not a claim the copy may make
    assert offer_facts(p, "Grain-free dog food for seniors.") == ["Grain-free", "Ships in 6 weeks."]


class Scripted(FakeProvider):
    """Writer returns two candidates; the critic passes only those whose headline says 'good'."""
    def __init__(self, heads):
        super().__init__({})
        self.heads = heads

    def complete(self, system, user, temperature):
        if "Candidates:" in user:
            cands = json.loads(user.split("Candidates:")[1].split("Return ONLY")[0])
            return json.dumps({"reviews": [{"persona_id": c["persona_id"], "variant": c["variant"], "punch": 5 if "good" in c["headline"] else 2,
                                            "persona_fit": 5 if "good" in c["headline"] else 2, "claims_ok": True,
                                            "distinct_from_others": True, "passed": "good" in c["headline"]} for c in cands]})
        return json.dumps({"variants": [{"persona_id": "persona_004", "variant": i + 1, "headline": h, "headline_no_name": h,
                                         "body": "Grain-free.", "cta": "Shop now", "angle": f"hook{i}"} for i, h in enumerate(self.heads)]})


def run(heads):
    sel = [PersonaMatch(persona_id="persona_004", persona_name="Pet Parent", score=0.9, selected=True, reasons=[])]
    return generate_creatives(profile(), sel, [], Scripted(heads), "Grain-free dog food.")


def test_a_failed_line_is_dropped_when_the_persona_has_a_passing_one():
    variants, reviews, log = run(["good one", "weak one"])
    assert [v.headline for v in variants] == ["good one"] and all(r.passed for r in reviews) and log["dropped"]


def test_when_nothing_passes_only_the_best_line_ships_and_is_marked():
    variants, reviews, _ = run(["weak one", "weak two"])
    assert len(variants) == 1 and not reviews[0].passed and variants[0].variant == 1
