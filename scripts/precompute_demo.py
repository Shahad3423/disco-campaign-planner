"""Run the planner for all 15 example advertisers and save the plans for the hosted demo.

    python scripts/precompute_demo.py

Output: demo_cache/plans/<n>.json (CampaignPlan JSON). The LLM cache (cache/llm) makes
re-runs nearly free. The Streamlit app falls back to these files when no API key is set
or a live call fails.
"""
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    provider = default_provider()
    for n, text in load_examples().items():
        plan = run_with_retries(text, provider)
        if plan is None:
            print(f"#{n}: FAILED after retries, skipped", flush=True)
            continue
        (OUT / f"{n}.json").write_text(plan.model_dump_json(indent=1))
        print(f"#{n}: gate={plan.profile.gate} publishers={sum(p.recommended for p in plan.publishers)}", flush=True)


if __name__ == "__main__":
    main()
