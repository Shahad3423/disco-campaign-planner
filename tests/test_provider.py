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
