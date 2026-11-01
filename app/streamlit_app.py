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
from disco_planner.planning import pipeline
from disco_planner.serving.features import ShopperSignals, build_features

st.set_page_config(page_title="Disco campaign planner", layout="wide")
ui.inject_css()
st.title("Disco campaign planner")
st.caption("Turn a short advertiser brief into a reviewable campaign: publishers, personas, creatives, budget.")

tab_plan, tab_sim, tab_eval, tab_how = st.tabs(["Plan a campaign", "Shopper simulator", "Evaluation", "How it works"])

# ---------------------------------------------------------------- plan page


UNIT = {"conversions": "conversions", "clicks": "clicks", "impressions": "impressions", "reach": "unique shoppers"}


def optional_inputs() -> AdvertiserInputs:
    """Campaign setup. The budget is the advertiser's to choose (never suggested): without it we still
    rank hosts, pick personas and write creatives, but don't allocate or forecast."""
    with st.expander("Campaign setup: budget, objective, bidding", expanded=True):
        b1, b2, b3 = st.columns(3)
        budget = b1.number_input("Budget for the flight (USD)", min_value=0.0, value=0.0, step=500.0,
                                 help="Your hard spending cap. We don't suggest one; leave 0 to plan without allocating.")
        flight = b2.number_input("Flight days", min_value=0, value=0, step=7, help="0 = 30 days")
        objective = b3.selectbox("Objective", ["conversions", "clicks", "impressions", "reach"],
                                 help="Conversions/clicks are bought per click; impressions/reach per 1,000 views.")
        g1, g2 = st.columns(2)
        goal = g1.number_input(f"Goal (optional): {UNIT[objective]}", min_value=0, value=0, step=100,
                               help="A target on top of the budget. We forecast whether the budget gets there.")
        when = g2.radio("When the goal is reached", ["stop (save the rest)", "keep spending"], horizontal=True,
                        disabled=goal == 0)
        k1, k2, k3 = st.columns(3)
        bidding = k1.selectbox("Bidding", ["maximize", "target_cpa", "target_roas"],
                               format_func={"maximize": "Maximise within budget", "target_cpa": "Target CPA",
                                            "target_roas": "Target ROAS"}.get,
                               disabled=objective in ("impressions", "reach"),
                               help="Targets are averages the bidder aims at, not per-click limits.")
        target_cpa = k2.number_input("Target CPA (USD)", min_value=0.0, value=0.0, step=5.0, disabled=bidding != "target_cpa")
        target_roas = k2.number_input("Target ROAS (x)", min_value=0.0, value=0.0, step=0.5, disabled=bidding != "target_roas")
        max_cpc = k3.number_input("Max CPC (USD, optional)", min_value=0.0, value=0.0, step=0.25,
                                  help="Your own hard per-click limit. 0 = no limit beyond the aggressiveness setting.")
        e1, e2, e3, e4 = st.columns(4)
        aggressiveness = e1.selectbox("How aggressively to bid", ["balanced", "conservative", "aggressive"],
                                      help="The most of each host's page views to try to win: 30% / 60% / 90%. "
                                           "Winning more means outbidding stronger rivals, so each unit costs more.")
        share = e2.slider("Max share of budget per host", 0.2, 1.0, 1.0, 0.05,
                          help="Optional: spreads the risk of one host under-delivering (1.0 = off). "
                               "The plan shows what it costs, and warns if it leaves budget unspent.")
        price = e3.number_input("Typical price (USD)", min_value=0.0, value=0.0, step=5.0,
                                help="Only needed for target ROAS; 0 = stated or tier default")
        tracking = e4.checkbox("Has conversion tracking")
    return AdvertiserInputs(
        monthly_budget_usd=budget or None, flight_days=int(flight) or None, objective=objective,
        goal_target=int(goal) or None, when_goal_met="stop" if when.startswith("stop") else "continue",
        bidding=bidding if objective in ("conversions", "clicks") else "maximize",
        target_cpa_usd=target_cpa or None, target_roas=target_roas or None, max_cpc_usd=max_cpc or None,
        aggressiveness=aggressiveness, max_share_per_host=share,
        price_usd=price or None, has_conversion_tracking=tracking,
    )


def run_and_store(brief: str, inputs: AdvertiserInputs, live: bool = False) -> None:
    label = "Planning live (fresh LLM calls, ~30-60s)" if live else "Planning: intake, publisher scoring, personas, creatives, budget..."
    with st.status(label, expanded=False) as status:
        plan, notice, model = ui.plan_campaign(brief, inputs, live=live)
        status.update(label="Done" if plan else "Could not plan", state="complete" if plan else "error")
    st.session_state["notice"] = notice
    if plan:
        st.session_state["plan"], st.session_state["model"], st.session_state["brief"] = plan, model, brief
        st.session_state.setdefault("session_plans", []).append(plan)
        # a live run shows everything but the creatives first; a cached fallback already has them
        st.session_state["creatives_pending"] = model != "cached run" and pipeline.needs_creatives(plan)


with tab_plan:
    examples = ui.load_examples()
    choice = st.selectbox("Start from an example advertiser", ["(write my own)"] + [f"#{n}: {t[:70]}..." for n, t in examples.items()])
    default_brief = examples[int(choice.split(":")[0][1:])] if choice.startswith("#") else ""
    # Changing the selectbox must replace the text area, so key it on the choice.
    brief = st.text_area("Advertiser brief", value=default_brief, height=110, key=f"brief_{choice}",
                         placeholder="Describe the product, who it's for, price, and any claims you can make.")
    inputs = optional_inputs()
    live = st.toggle("Run live (skip the LLM cache)", value=False,
                     help="Off: identical inputs reuse earlier LLM answers, so results are reproducible and instant. "
                          "On: fresh Gemini calls end to end (slower); nothing is written to the cache.")
    if st.button("Plan campaign", type="primary", disabled=not brief.strip()):
        run_and_store(brief.strip(), inputs, live=live)

    if st.session_state.get("notice"):
        st.warning(st.session_state["notice"])
    plan = st.session_state.get("plan")
    if plan:
        qa = ui.render_clarify_form(plan)
        if qa:
            run_and_store(ui.brief_with_answers(st.session_state["brief"], qa), inputs, live=live)
            st.rerun()
        pending = st.session_state.get("creatives_pending", False)
        ui.render_plan(plan, st.session_state.get("model", "unknown"), creatives_pending=pending)
        if pending:  # the page above is already on screen; the slow creative step runs now, then the page refreshes
            with st.spinner("Writing creatives..."):
                plan, notice, calls = ui.add_creatives_live(plan, live)
            session = st.session_state["session_plans"]
            session[-1] = plan
            st.session_state.update(plan=plan, creatives_pending=False, notice=notice,
                                    model=f"{st.session_state['model']}; creatives: {calls}" if calls else st.session_state["model"])
            st.rerun()

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
    c7, c8 = st.columns(2)
    phase = c7.selectbox("Bid phase", ["learning", "enhanced_cpc"],
                         help="learning: fixed start bids; enhanced_cpc: bid up for shoppers who look likely to convert")
    pacing = c8.slider("Pacing (spent share / time share)", 0.5, 1.5, 1.0, 0.05, help="<1 under-delivering, >1 over-delivering")
    signals = ShopperSignals(publisher_id=pub_id, zip=zip_code, first_name=first_name, zip_source=zip_source)
    now_utc = datetime.combine(day, clock, tzinfo=timezone.utc)

    st.subheader("Features")
    ui.render_features(build_features(signals, now_utc))

    st.subheader("Decision")
    plans = st.session_state.get("session_plans", []) + ui.load_all_cached_plans()
    holdout = ui.load_holdout_plans()
    if holdout and st.checkbox(f"Add the {len(holdout)} holdout brands as competing advertisers", value=True):
        plans = plans + holdout
    # Per-host budget caps: mark campaigns whose budget on THIS host is used up.
    here = {}
    for p in plans:
        alloc = next((x for x in (p.config.budget.allocations if p.config.budget else []) if x.publisher_id == pub_id), None)
        if alloc and p.config.status == "draft":
            here[p.profile.product or p.brief[:40]] = alloc.budget_usd
    out_of_budget = st.multiselect(
        f"Campaigns that have used up their budget on {catalog.publisher_by_id()[pub_id].name}", sorted(here),
        help="A campaign drops out of THIS host's auction when its allocated budget here is spent; it still bids on its other hosts")
    spent = {(name, pub_id): here[name] for name in out_of_budget}
    blocks = st.checkbox("This host blocks brands that sell what it sells", value=False,
                         help="A host setting (D21). Off by default: most hosts allow non-identical brands.")
    decision, note = ui.try_serve(signals, now_utc, plans, phase=phase, pacing_ratio=pacing, spent_usd=spent,
                                  host_blocks_competitors=blocks)
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
