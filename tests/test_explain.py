"""Plain-English reasons (planning/explain.py): what advertisers read must not leak internal names, on every stored brand."""
from pathlib import Path

import pytest

from disco_planner.domain.models import AdvertiserInputs
from disco_planner.planning import pipeline
from disco_planner.planning.pipeline import CampaignPlan

ROOT = Path(__file__).resolve().parents[1]
TECHNICAL = ["_fit", "(multiplier", "(specific)", "category only)", "attribute tags only", "host fit", "AOV",
             "competitor_conflict", "weak_network_fit", "audience_note_conflict", "affinity", "cutoff", "Flags:"]
PLANS = sorted((ROOT / "demo_cache" / "plans").glob("*.json")) + sorted((ROOT / "reports" / "holdout_plans").glob("*.json"))


@pytest.mark.parametrize("path", PLANS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_reasons_are_plain_english(path):
    stored = CampaignPlan.model_validate_json(path.read_text())
    plan = pipeline.plan_from_profile(stored.brief, stored.profile, pipeline.date(2026, 10, 30), AdvertiserInputs(monthly_budget_usd=5000))
    texts = [s.reason for s in plan.publishers] + [r for m in plan.personas for r in m.reasons]
    if plan.config.budget:
        texts += [a.bid_reason for a in plan.config.budget.allocations]
    leaks = [(t, w) for t in texts for w in TECHNICAL if w in t]
    assert not leaks, leaks[:3]
