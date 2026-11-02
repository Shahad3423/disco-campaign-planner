"""Run the planner for all 15 example advertisers and save the plans for the hosted demo.

    python scripts/precompute_demo.py              # full run (LLM intake + creatives, cached where possible)
    python scripts/precompute_demo.py --keep-llm   # refresh only the code-computed parts of the stored plans
    python scripts/precompute_demo.py --creatives  # keep each stored intake, re-run the code and rewrite the creatives (LLM)

--keep-llm keeps each stored plan's intake profile and creatives and recomputes hosts, personas, budget, bids and
config with today's code: no LLM calls, so the demo follows code changes without new copy.
Output: demo_cache/plans/<n>.json (CampaignPlan JSON). The LLM cache (cache/llm) makes
re-runs nearly free. The Streamlit app falls back to these files when no API key is set
or a live call fails.
"""
import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

from components import load_examples  # noqa: E402
from disco_planner.domain.models import AdvertiserInputs
from disco_planner.llm.provider import default_provider  # noqa: E402
from disco_planner.planning import pipeline  # noqa: E402
from disco_planner.planning.pipeline import CampaignPlan  # noqa: E402

OUT = ROOT / "demo_cache" / "plans"
TODAY = date(2026, 10, 30)  # same planning date as the eval report


def run_with_retries(text, provider, attempts=4):
    """Free-tier models sometimes return malformed JSON or 503; earlier stages are LLM-cached, so retries are cheap."""
    for _ in range(attempts):
        try:
            # D23: the advertiser sets the budget; cached demo plans use a stated $5,000 demo budget
            return pipeline.run(text, provider, TODAY, AdvertiserInputs(monthly_budget_usd=5000))
        except Exception as e:
            print(f"   retrying after {type(e).__name__}: {str(e)[:100]}", flush=True)
            time.sleep(3)
    return None


def refresh(path: Path) -> None:
    old = CampaignPlan.model_validate_json(path.read_text())
    plan = pipeline.plan_from_profile(old.brief, old.profile, TODAY, AdvertiserInputs(monthly_budget_usd=5000))
    if old.creatives:
        plan = pipeline.attach_creatives(plan, old.creatives, old.reviews, old.creative_log)
    plan = plan.model_copy(update={"timings_s": {**old.timings_s, **{k: v for k, v in plan.timings_s.items() if k == "matching"}}})
    selected = {m.persona_id for m in plan.personas if m.selected}
    written = {v.persona_id for v in old.creatives}
    note = "" if not written or selected == written else f"  WARNING personas now {sorted(selected)}, creatives for {sorted(written)}"
    path.write_text(plan.model_dump_json(indent=1))
    b = plan.config.budget
    print(f"#{path.stem}: refreshed" + (f", spend ${b.planned_spend_usd:,.0f}, {b.forecast.mid:,.1f} {b.unit}" if b else "") + note, flush=True)


def recreate(path: Path, provider) -> None:
    old = CampaignPlan.model_validate_json(path.read_text())
    plan = pipeline.plan_from_profile(old.brief, old.profile, TODAY, AdvertiserInputs(monthly_budget_usd=5000))
    plan = pipeline.add_creatives(plan, provider, TODAY)
    plan = plan.model_copy(update={"timings_s": {**old.timings_s, **plan.timings_s}})
    path.write_text(plan.model_dump_json(indent=1))
    passed = sum(r.passed for r in plan.reviews)
    print(f"#{path.stem}: creatives rewritten, {passed}/{len(plan.reviews)} passed review", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-llm", action="store_true", help="recompute only the code parts of the stored plans (no LLM)")
    ap.add_argument("--creatives", action="store_true", help="keep the stored intake; recompute the code parts and rewrite creatives")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.keep_llm:
        for path in sorted(OUT.glob("*.json"), key=lambda p: int(p.stem)):
            refresh(path)
        return
    provider = default_provider()
    if args.creatives:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda p: recreate(p, provider), sorted(OUT.glob("*.json"), key=lambda p: int(p.stem))))
        return
    for n, text in load_examples().items():
        plan = run_with_retries(text, provider)
        if plan is None:
            print(f"#{n}: FAILED after retries, skipped", flush=True)
            continue
        (OUT / f"{n}.json").write_text(plan.model_dump_json(indent=1))
        print(f"#{n}: gate={plan.profile.gate} publishers={sum(p.recommended for p in plan.publishers)}", flush=True)


if __name__ == "__main__":
    main()
