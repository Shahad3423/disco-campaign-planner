from disco_planner.domain.models import CreativeReview
from disco_planner.llm.provider import FakeProvider, generate
from disco_planner.planning.creative import ReviewSet

ONE = '{"persona_id": "persona_004", "persona_fit": 5, "claims_ok": true, "distinct_from_others": true, "passed": true}'


def test_single_item_is_wrapped_into_list_schema():
    out = generate(FakeProvider({"x": ONE}), "sys", "x", ReviewSet, use_cache=False)
    assert isinstance(out.reviews[0], CreativeReview)


def test_bare_list_is_wrapped():
    out = generate(FakeProvider({"x": f"```json\n[{ONE}]\n```"}), "sys", "x", ReviewSet, use_cache=False)
    assert len(out.reviews) == 1


def test_invalid_output_retries_then_raises():
    import pytest
    from disco_planner.llm.provider import LLMError
    with pytest.raises(LLMError):
        generate(FakeProvider({"x": '{"nope": 1}'}), "sys", "x", ReviewSet, use_cache=False)


def test_regulated_claims_are_detected():
    from disco_planner.planning.claims import violations
    assert violations("Clinically proven to cause weight loss")
    assert violations("FDA approved bars")
    assert not violations("Vet-formulated, grain-free nutrition")


def test_claims_field_is_owned_by_code():
    # The model put ordinary claims in claims_needing_substantiation; code moves them back and
    # keeps only policy matches blocked.
    import json
    from datetime import date
    from disco_planner.llm.provider import FakeProvider
    from disco_planner.planning.intake import parse_brief
    profile = {
        "business_summary": "Self-cleaning litter box", "product": "litter box", "is_consumer_product": True,
        "stated_claims": ["Works with any litter"], "claims_needing_substantiation": ["Self-cleaning", "Vet-recommended"],
        "relevant_tags": [{"tag": "pet_supplies", "weight": 1, "why": "x"}],
        "confidence": 0.9, "gate": "proceed", "gate_reason": "clear",
    }
    p = parse_brief("We sell a self-cleaning litter box for cats", FakeProvider({"litter": json.dumps(profile)}), date(2026, 10, 30), n_samples=1)
    assert "Self-cleaning" in p.stated_claims
    assert p.claims_needing_substantiation == ["Vet-recommended"]
