"""Streamlit entry point: `streamlit run app/streamlit_app.py`.

Pages are tabs; all drawing code lives in components.py.
"""
import sys
from datetime import datetime, time, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so `import components` works anywhere

import streamlit as st

import components as ui
from disco_planner.data import catalog
from disco_planner.domain.models import AdvertiserInputs
from disco_planner.serving.features import ShopperSignals, build_features

st.set_page_config(page_title="Disco campaign planner", layout="wide")
ui.inject_css()
st.title("Disco campaign planner")
st.caption("Turn a short advertiser brief into a reviewable campaign: publishers, personas, creatives, budget.")

tab_plan, tab_sim, tab_eval, tab_how = st.tabs(["Plan a campaign", "Shopper simulator", "Evaluation", "How it works"])

# ---------------------------------------------------------------- plan page


def optional_inputs() -> AdvertiserInputs:
    """Optional-details form. Blank / zero means 'use the labelled default'."""
    with st.expander("Optional details (override our assumptions)"):
        c1, c2, c3 = st.columns(3)
        budget = c1.number_input("Monthly budget (USD)", min_value=0.0, value=0.0, step=500.0, help="0 = recommend a learning budget")
        price = c2.number_input("Typical price (USD)", min_value=0.0, value=0.0, step=5.0, help="0 = use stated or tier default")
        margin = c3.number_input("Gross margin (%)", min_value=0.0, max_value=100.0, value=0.0, step=5.0, help="0 = category default")
        c4, c5, c6 = st.columns(3)
        objective = c4.selectbox("Objective", ["acquisition", "awareness"])
        flight = c5.number_input("Flight days", min_value=0, value=0, step=7, help="0 = default")
        tracking = c6.checkbox("Has conversion tracking")
    return AdvertiserInputs(
        monthly_budget_usd=budget or None, price_usd=price or None, gross_margin=(margin / 100) or None,
        objective=objective, flight_days=int(flight) or None, has_conversion_tracking=tracking,
    )


def run_and_store(brief: str, inputs: AdvertiserInputs) -> None:
    with st.status("Planning: intake, publisher scoring, personas, creatives, budget...", expanded=False) as status:
        plan, notice, model = ui.plan_campaign(brief, inputs)
        status.update(label="Done" if plan else "Could not plan", state="complete" if plan else "error")
    st.session_state["notice"] = notice
    if plan:
        st.session_state["plan"], st.session_state["model"], st.session_state["brief"] = plan, model, brief
        st.session_state.setdefault("session_plans", []).append(plan)


with tab_plan:
    examples = ui.load_examples()
    choice = st.selectbox("Start from an example advertiser", ["(write my own)"] + [f"#{n}: {t[:70]}..." for n, t in examples.items()])
    default_brief = examples[int(choice.split(":")[0][1:])] if choice.startswith("#") else ""
    # Changing the selectbox must replace the text area, so key it on the choice.
    brief = st.text_area("Advertiser brief", value=default_brief, height=110, key=f"brief_{choice}",
                         placeholder="Describe the product, who it's for, price, and any claims you can make.")
    inputs = optional_inputs()
    if st.button("Plan campaign", type="primary", disabled=not brief.strip()):
        run_and_store(brief.strip(), inputs)

    if st.session_state.get("notice"):
        st.warning(st.session_state["notice"])
    plan = st.session_state.get("plan")
    if plan:
        qa = ui.render_clarify_form(plan)
        if qa:
            run_and_store(ui.brief_with_answers(st.session_state["brief"], qa), inputs)
            st.rerun()
        ui.render_plan(plan, st.session_state.get("model", "unknown"))

# ---------------------------------------------------------------- shopper simulator

with tab_sim:
    st.markdown("Serving stage: runs in milliseconds with **no LLM**. Only three signals arrive: publisher, zip code, first name.")
    pubs = catalog.publishers()
    c1, c2, c3 = st.columns(3)
    pub_id = c1.selectbox("Publisher", [p.id for p in pubs], format_func=lambda i: f"{catalog.publisher_by_id()[i].name} ({i})")
    zip_code = c2.text_input("Zip code", "10001")
    first_name = c3.text_input("First name", "Sarah")
    c4, c5, c6 = st.columns(3)
    zip_source = c4.selectbox("Zip source", ["billing", "shipping", "unknown"])
    day = c5.date_input("Request date (UTC)", value=ui.PLANNING_DATE)
    clock = c6.time_input("Request time (UTC)", value=time(18, 0))
    signals = ShopperSignals(publisher_id=pub_id, zip=zip_code, first_name=first_name, zip_source=zip_source)
    now_utc = datetime.combine(day, clock, tzinfo=timezone.utc)

    st.subheader("Features")
    ui.render_features(build_features(signals, now_utc))

    st.subheader("Decision")
    plans = st.session_state.get("session_plans", []) + ui.load_all_cached_plans()
    decision, note = ui.try_serve(signals, now_utc, plans)
    if decision is not None:
        ui.render_decision(decision, plans)
    else:
        st.info(f"{note} ({len(plans)} plans available to the simulator.)")

# ---------------------------------------------------------------- evaluation

with tab_eval:
    reports = ui.eval_reports()
    if reports:
        label = st.selectbox("Report", list(reports))
        ui.render_eval(reports[label])
    else:
        st.info("No reports/eval_*.md found. Run `python -m disco_planner.eval.run`.")

# ---------------------------------------------------------------- how it works

with tab_how:
    st.subheader("Two stages, two budgets")
    st.markdown("""
| | Stage 1: planning | Stage 2: serving |
|---|---|---|
| Runs | once per advertiser | once per impression |
| Latency | seconds are fine | tens of milliseconds |
| LLM | yes (intake, creatives, critic) | **no** |
| Output | `CampaignPlan` / `CampaignConfig` | one offer, rendered safely |
""")
    st.code("brief -> intake (LLM) -> gate -> publisher scoring (code) -> personas -> creatives (LLM) + critic -> budget + bids (code) -> CampaignConfig\n"
            "shopper {publisher, zip, first name} -> features -> persona posterior -> eligible campaigns -> rank -> render", language=None)
    st.markdown("The LLM handles language; code handles numbers and ranking.")
    st.subheader("Design decisions")
    for title, anchor, body in ui.decision_sections():
        with st.expander(title):
            st.markdown(body)
            st.markdown(f"[Open in repo]({ui.REPO_URL}/blob/main/docs/DECISIONS.md#{anchor})")
