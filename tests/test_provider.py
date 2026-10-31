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


def test_live_mode_neither_reads_nor_writes_cache(tmp_path, monkeypatch):
    from disco_planner.llm import provider as prov

    class Counting:
        name, model = "counting", "m"
        def __init__(self):
            self.calls = 0
        def complete(self, system, user, temperature):
            self.calls += 1
            return ONE

    monkeypatch.setattr(prov, "CACHE_DIR", tmp_path)
    p = Counting()
    stats_token = prov.CALL_STATS.set({})
    prov.generate(p, "s", "u", ReviewSet)            # first call: live, then cached on disk
    prov.generate(p, "s", "u", ReviewSet)            # same input: served from cache
    assert p.calls == 1 and prov.CALL_STATS.get() == {"live": 1, "cached": 1}
    files_before = sorted(tmp_path.iterdir())
    token = prov.CACHE_ENABLED.set(False)             # live mode
    try:
        prov.generate(p, "s", "u", ReviewSet)        # must call the model again ...
        prov.generate(p, "s", "u2", ReviewSet)       # ... and write nothing new
    finally:
        prov.CACHE_ENABLED.reset(token)
        prov.CALL_STATS.reset(stats_token)
    assert p.calls == 3
    assert sorted(tmp_path.iterdir()) == files_before
