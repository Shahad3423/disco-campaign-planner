"""Smoke tests for the Streamlit app: the page loads and every cached plan renders."""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import components as ui  # noqa: E402


def test_examples_parse():
    assert len(ui.load_examples()) == 15


def test_every_cached_plan_loads_and_matches_its_example():
    for n, text in ui.load_examples().items():
        plan = ui.load_cached_plan(n)
        assert plan is not None, f"missing demo_cache/plans/{n}.json"
        assert ui.find_example_number(plan.brief) == n


def test_main_page_runs_without_exception():
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=30).run()
    assert not at.exception


@pytest.mark.parametrize("n", range(1, 16))
def test_render_plan_for_each_cached_example(n):
    """Render the plan inside a tiny AppTest script so Streamlit calls are valid."""
    script = f"""
import sys; sys.path.insert(0, r"{APP}")
import components as ui
plan = ui.load_cached_plan({n})
ui.render_plan(plan, "cached run")
"""
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception


def test_shopper_features_render():
    script = f"""
import sys; sys.path.insert(0, r"{APP}")
from datetime import datetime, timezone
import components as ui
from disco_planner.serving.features import ShopperSignals, build_features
f = build_features(ShopperSignals(publisher_id="pub_001", zip="10001", first_name="Sarah", zip_source="billing"),
                   datetime(2026, 10, 30, 18, tzinfo=timezone.utc))
ui.render_features(f)
"""
    assert not AppTest.from_string(script, default_timeout=30).run().exception
