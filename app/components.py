"""Rendering and data helpers for the Streamlit app.

Everything the UI draws lives here as a small function, so tests can call them and so
there is one obvious place to change how something looks. `streamlit_app.py` only wires
pages together.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # lets Streamlit Cloud import the package without installing it

from disco_planner.domain.models import AdvertiserInputs  # noqa: E402
from disco_planner.llm.provider import LLMError, default_provider  # noqa: E402
from disco_planner.planning import pipeline  # noqa: E402
from disco_planner.planning.pipeline import CampaignPlan  # noqa: E402

PLANNING_DATE = date(2026, 10, 30)  # matches the eval report and the cached demo plans
DEMO_DIR = ROOT / "demo_cache" / "plans"
ACCENT = "#2F5D8A"
REPO_URL = "https://github.com/Shahad3423/disco-campaign-planner"

# ------------------------------------------------------------------ styling

CSS = f"""
<style>
.bar-track {{background:#E6E9EE;border-radius:4px;height:8px;width:100%;}}
.bar-fill {{background:{ACCENT};border-radius:0 4px 4px 0;height:8px;}}
.badge {{display:inline-block;padding:1px 8px;border-radius:10px;font-size:0.75rem;margin-right:4px;
        background:#EEF1F5;color:#3E4C59;border:1px solid #D5DAE1;}}
.badge.warn {{background:#FFF4E0;color:#7A4B00;border-color:#F1D39A;}}
.badge.assumed {{background:#F1ECFA;color:#4B3B7A;border-color:#D4C8EE;}}
.badge.ok {{background:#E8F4EC;color:#1E5B34;border-color:#BCDDC8;}}
.badge.bad {{background:#FCE9E9;color:#7A1F1F;border-color:#EBBABA;}}
.offer-card {{border:1px solid #D5DAE1;border-radius:10px;padding:14px 16px;background:#fff;
             max-width:360px;box-shadow:0 1px 2px rgba(0,0,0,0.05);}}
.offer-head {{font-size:0.7rem;letter-spacing:0.04em;text-transform:uppercase;color:#7B8794;margin-bottom:8px;}}
.offer-title {{font-weight:600;font-size:1.02rem;color:#1F2933;margin-bottom:6px;}}
.offer-body {{font-size:0.88rem;color:#52606D;margin-bottom:12px;}}
.offer-cta {{display:inline-block;background:{ACCENT};color:#fff;padding:6px 14px;border-radius:6px;font-size:0.85rem;}}
.muted {{color:#7B8794;font-size:0.85rem;}}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{html.escape(text)}</span>'


def bar(value: float, maximum: float = 1.0) -> str:
    """A thin horizontal bar as HTML. One colour, since bars encode magnitude only."""
    pct = 0 if maximum <= 0 else max(0.0, min(1.0, value / maximum)) * 100
    return f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.0f}%"></div></div>'


# ------------------------------------------------------------------ examples and demo cache


def load_examples() -> dict[int, str]:
    """Parse data/example_advertisers.txt: lines like '12. We sell ...'."""
    text = (ROOT / "data" / "example_advertisers.txt").read_text()
    return {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"^(\d+)\.\s+(.+)$", text, re.M)}


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def find_example_number(brief: str) -> int | None:
    for n, text in load_examples().items():
        if _norm(text) == _norm(brief):
            return n
    return None


def load_cached_plan(n: int) -> CampaignPlan | None:
    path = DEMO_DIR / f"{n}.json"
    return CampaignPlan.model_validate_json(path.read_text()) if path.exists() else None


def load_all_cached_plans() -> list[CampaignPlan]:
    return [p for n in load_examples() if (p := load_cached_plan(n))]


# ------------------------------------------------------------------ running the planner


CREDENTIAL_NAMES = ("VERTEX_API_KEY", "GEMINI_API_KEY")  # Vertex (cloud credits) first, free AI Studio key as backup


def get_api_key() -> str | None:
    """Streamlit Cloud secrets first, then environment / .env (loaded by the provider module).
    Copies any secrets found into the environment so default_provider() builds the Vertex -> AI Studio chain."""
    for name in CREDENTIAL_NAMES:
        try:
            value = st.secrets.get(name)
        except Exception:  # no secrets file locally
            value = None
        if value:
            os.environ[name] = value
    return next((os.environ[n] for n in CREDENTIAL_NAMES if os.environ.get(n)), None)


def describe_calls(provider, stats: dict) -> str:
    """Truthful footer: how many LLM answers were fresh calls vs reused from the cache."""
    live, cached = stats.get("live", 0), stats.get("cached", 0)
    backend = getattr(provider, "last_backend_used", None) or getattr(provider, "backend", None)
    model = getattr(provider, "last_model_used", None) or getattr(provider, "model", "")
    parts = []
    if live:
        parts.append(f"{live} live LLM call(s): {model}" + (f" via {backend}" if backend else ""))
    if cached:
        parts.append(f"{cached} answer(s) reused from the LLM cache (same model, prompt and input)")
    return "; ".join(parts) or "no LLM calls needed"


def plan_campaign(brief: str, inputs: AdvertiserInputs, live: bool = False) -> tuple[CampaignPlan | None, str | None, str]:
    """Return (plan, notice, model_label). Falls back to a cached plan when the brief is an
    example and live planning is unavailable (no key, quota, 503, bad output)."""
    notice, error = None, None
    if not get_api_key():
        error = "No LLM credentials configured (VERTEX_API_KEY or GEMINI_API_KEY)."
    else:
        from disco_planner.llm.provider import CACHE_ENABLED, CALL_STATS

        cache_token = CACHE_ENABLED.set(not live)  # live = fresh LLM calls, no cache read or write
        stats_token = CALL_STATS.set({})
        try:
            provider = default_provider()
            plan = pipeline.run(brief, provider, PLANNING_DATE, inputs)
            return plan, None, describe_calls(provider, CALL_STATS.get())
        except Exception as e:  # LLMError, quota, network: any failure should degrade gracefully
            error = f"Live planning failed ({type(e).__name__}: {str(e)[:160]})."
        finally:
            CACHE_ENABLED.reset(cache_token)
            CALL_STATS.reset(stats_token)

    n = find_example_number(brief)
    cached = load_cached_plan(n) if n else None
    if cached:
        notice = f"{error} Showing a cached run for example advertiser #{n}. Economics inputs from the form are not applied to cached runs."
        return cached, notice, "cached run"
    return None, f"{error} Cached runs exist only for the 15 example briefs, so this custom brief cannot be planned right now.", "none"


# ------------------------------------------------------------------ plan page


GATE_BANNERS = {
    "proceed": ("success", "Ready to proceed. This is a draft plan for the advertiser to review and approve."),
    "clarify": ("warning", "We need a bit more information. Answer the questions below and re-plan. The draft shown is provisional."),
    "reject_low_signal": ("error", "Not enough signal to plan. The brief is too vague to match to publishers or personas, and guessing would waste the advertiser's budget."),
    "off_network": ("info", "Off-network. This is not a consumer product that fits post-purchase offers on DTC checkout pages, so recommending zero publishers is the correct answer, not a failure."),
}


def render_gate_banner(plan: CampaignPlan) -> None:
    kind, text = GATE_BANNERS[plan.profile.gate]
    getattr(st, kind)(f"**{plan.profile.gate.replace('_', ' ').capitalize()}.** {text}\n\n{plan.profile.gate_reason}")


def render_clarify_form(plan: CampaignPlan) -> list[tuple[str, str]] | None:
    """Show clarifying questions as inputs; returns Q/A pairs when the user submits."""
    questions = plan.profile.clarifying_questions
    if plan.profile.gate != "clarify" or not questions:
        return None
    with st.form("clarify_form"):
        st.markdown("**Clarifying questions**")
        answers = [st.text_input(q, key=f"clarify_{i}") for i, q in enumerate(questions)]
        submitted = st.form_submit_button("Re-plan with answers")
    if submitted:
        return [(q, a) for q, a in zip(questions, answers) if a.strip()]
    return None


def brief_with_answers(brief: str, qa: list[tuple[str, str]]) -> str:
    return brief + "\n\nClarifications from the advertiser:\n" + "\n".join(f"Q: {q}\nA: {a}" for q, a in qa)


def render_understanding(plan: CampaignPlan) -> None:
    p = plan.profile
    st.subheader("What we understood")
    st.write(p.business_summary)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Price tier", p.price_tier)
    c2.metric("Purchase model", p.purchase_model.replace("_", " "))
    c3.metric("Price point", f"${p.price_point_usd:,.0f}" if p.price_point_usd else "not stated")
    c4.metric("Confidence", f"{p.confidence:.0%}")
    if p.stated_claims:
        st.markdown("**Claims the advertiser made** (the only claims creatives may use)")
        st.markdown(" ".join(badge(c) for c in p.stated_claims), unsafe_allow_html=True)
    if p.relevant_tags:
        st.markdown("**Relevant publisher tags**")
        st.dataframe(
            pd.DataFrame([{"tag": t.tag, "weight": t.weight, "why": t.why} for t in sorted(p.relevant_tags, key=lambda t: -t.weight)]),
            hide_index=True, width="stretch",
            column_config={"weight": st.column_config.ProgressColumn("weight", min_value=0, max_value=1, format="%.2f")},
        )
    if p.competing_tags:
        st.markdown("**Competing tags** (hosts that sell the same thing)  \n" + " ".join(badge(t, "warn") for t in p.competing_tags), unsafe_allow_html=True)
    if p.missing_info:
        st.markdown("**Missing information**")
        for m in p.missing_info:
            st.markdown(f"- {m}")
    render_economics(plan)


def render_economics(plan: CampaignPlan) -> None:
    eco = plan.config.economics
    if not eco:
        return
    st.markdown("**Economics** " + badge("assumed", "assumed") + " <span class='muted'>override these in Optional details</span>", unsafe_allow_html=True)
    rows = [
        ("Price", f"${eco.price_usd:,.2f}", eco.price_source),
        ("Target CPA from", eco.cpa_source.replace("_", " "), "form" if eco.cpa_source != "margin" else eco.margin_source),
        ("Gross margin", f"{eco.gross_margin:.0%}" if eco.gross_margin is not None else "not needed", eco.margin_source),
        ("Expected orders (month)", f"{eco.expected_orders:,.0f}", "model"),
        ("Max affordable CAC", f"${eco.max_affordable_cac:,.2f}", "derived"),
        ("Target CPA", f"${eco.target_cpa:,.2f}", "derived"),
    ]
    df = pd.DataFrame(rows, columns=["item", "value", "source"])
    df["status"] = df["source"].map(lambda s: "assumed" if s in ("tier_default", "category_default", "model") else "provided or derived")
    st.dataframe(df, hide_index=True, width="stretch")
    if eco.assumptions:
        st.markdown(" ".join(badge("assumed", "assumed") + " " + html.escape(a) + "<br>" for a in eco.assumptions), unsafe_allow_html=True)


def render_component_breakdown(score) -> None:
    """One bar per score component, read generically so new components appear automatically."""
    for c in score.components:
        left, right = st.columns([2, 5])
        left.markdown(f"**{c.name.replace('_', ' ')}**  \n<span class='muted'>value {c.value:.2f}, weight {c.weight:g}</span>", unsafe_allow_html=True)
        right.markdown(bar(c.value) + f"<div class='muted'>{html.escape(c.detail)}</div>", unsafe_allow_html=True)


def flag_badges(flags: list[str]) -> str:
    labels = {"competitor_conflict": "needs host approval"}
    return " ".join(badge(labels.get(f, f.replace("_", " ")), "warn") for f in flags)


def render_publishers(plan: CampaignPlan) -> None:
    st.subheader("Publishers")
    recommended = sorted((s for s in plan.publishers if s.recommended), key=lambda s: -s.score)
    excluded = sorted((s for s in plan.publishers if not s.recommended), key=lambda s: -s.score)
    if not recommended:
        st.info("No publishers recommended.")
    for rank, s in enumerate(recommended, 1):
        with st.expander(f"{rank}. {s.publisher_name}  |  score {s.score:.2f}", expanded=rank <= 2):
            if s.flags:
                st.markdown(flag_badges(s.flags), unsafe_allow_html=True)
            st.write(s.reason)
            render_component_breakdown(s)
    st.markdown(f"**Excluded and why** ({len(excluded)})")
    if excluded:
        st.dataframe(
            pd.DataFrame([{"publisher": s.publisher_name, "score": round(s.score, 2), "reason": s.reason} for s in excluded]),
            hide_index=True, width="stretch",
        )
        with st.expander("Score components for excluded publishers"):
            for s in excluded:
                st.markdown(f"**{s.publisher_name}**")
                render_component_breakdown(s)


def render_personas(plan: CampaignPlan) -> None:
    st.subheader("Personas")
    selected = [m for m in plan.personas if m.selected]
    rejected = [m for m in plan.personas if not m.selected]
    names = {s.publisher_id: s.publisher_name for s in plan.publishers}
    if not selected:
        st.info("No personas selected.")
    cols = st.columns(min(3, max(1, len(selected))))
    for i, m in enumerate(sorted(selected, key=lambda m: -m.score)):
        with cols[i % len(cols)].container(border=True):
            st.markdown(f"**{m.persona_name}**  \n<span class='muted'>match {m.score:.2f}</span>", unsafe_allow_html=True)
            st.markdown(bar(m.score), unsafe_allow_html=True)
            for r in m.reasons:
                st.markdown(f"- {r}")
            if m.best_publishers:
                st.markdown("Best on: " + " ".join(badge(names.get(p, p)) for p in m.best_publishers), unsafe_allow_html=True)
    conflicts = {c.persona_id: c for c in plan.profile.persona_conflicts}
    if rejected:
        with st.expander(f"Excluded personas ({len(rejected)})"):
            for m in sorted(rejected, key=lambda m: -m.score):
                st.markdown(f"**{m.persona_name}** (score {m.score:.2f})")
                c = conflicts.get(m.persona_id)
                if c:
                    st.markdown(f"- Conflict: {c.conflict}  \n- Evidence from brief: *{c.evidence}*")
                for r in m.reasons:
                    st.markdown(f"- {r}")


# ------------------------------------------------------------------ creatives


def render_name(template: str, first_name: str | None) -> str:
    """Display-only substitution. Serving does this safely at impression time."""
    return template.replace("{first_name}", first_name) if first_name else template


def offer_card_html(headline: str, body: str, cta: str) -> str:
    return (
        '<div class="offer-card"><div class="offer-head">Offers from brands we like</div>'
        f'<div class="offer-title">{html.escape(headline)}</div>'
        f'<div class="offer-body">{html.escape(body)}</div>'
        f'<span class="offer-cta">{html.escape(cta)}</span></div>'
    )


def render_offer_card(headline: str, body: str, cta: str) -> None:
    st.markdown(offer_card_html(headline, body, cta), unsafe_allow_html=True)


def render_creatives(plan: CampaignPlan) -> None:
    st.subheader("Creatives")
    if not plan.creatives:
        st.info("No creatives were generated for this brief.")
        return
    mode = st.radio("Preview", ["With sample name", "Name unavailable"], horizontal=True, key="creative_mode")
    sample = st.text_input("Sample name", "Sarah", key="creative_name") if mode == "With sample name" else None
    st.caption("The stored creative keeps the `{first_name}` placeholder; the name is filled in at serve time, with the no-name headline as fallback.")
    reviews = {r.persona_id: r for r in plan.reviews}
    personas = {m.persona_id: m.persona_name for m in plan.personas}
    cols = st.columns(min(3, len(plan.creatives)))
    for i, v in enumerate(plan.creatives):
        with cols[i % len(cols)]:
            st.markdown(f"**{personas.get(v.persona_id, v.persona_id)}** <span class='muted'>angle: {html.escape(v.angle)}</span>", unsafe_allow_html=True)
            headline = render_name(v.headline, sample) if sample else v.headline_no_name
            render_offer_card(headline, v.body, v.cta)
            st.code(f"headline: {v.headline}\nno-name:  {v.headline_no_name}", language=None)
            r = reviews.get(v.persona_id)
            if r:
                verdict = badge("passed", "ok") if r.passed else badge("failed review", "bad")
                claims = badge("claims ok", "ok") if r.claims_ok else badge("unsupported claims", "bad")
                st.markdown(f"{verdict} {claims} {badge(f'persona fit {r.persona_fit}/5')}", unsafe_allow_html=True)
                for issue in [*r.issues, *[f"Unsupported: {c}" for c in r.unsupported_claims]]:
                    st.markdown(f"- {issue}")


# ------------------------------------------------------------------ campaign config


def render_auction_insights(plan: CampaignPlan) -> None:
    """Aggregate feedback an advertiser can act on without ever seeing a rival's bid."""
    from disco_planner.serving.insights import auction_insights

    st.markdown("**Auction insights** <span class='muted'>(sealed bids: you see win rate, price paid and what it would take, never a rival's bid)</span>",
                unsafe_allow_html=True)
    key = f"insights_{plan.brief[:60]}"
    rivals = [p for p in load_all_cached_plans() + load_holdout_plans() if (p.profile.product or p.brief[:40]) != (plan.profile.product or plan.brief[:40])]
    if st.button(f"Simulate against {len(rivals)} competing campaigns", key=f"btn_{key}"):
        st.session_state[key] = [i.model_dump() for i in auction_insights(plan, rivals)]
    rows = st.session_state.get(key)
    if rows:
        df = pd.DataFrame([{
            "host": r["publisher_name"], "win_rate": r["win_rate"], "avg_price_paid": r["avg_price_paid"], "your_bid": r["avg_bid"],
            "break_even_max": r["max_bid"], "rivals_here": r["competitors_on_host"],
            "top_rival": ", ".join(f"{n} ({s:.0%})" for n, s in r["top_rivals"][:2]),
            "bid_for_50%_wins": r["bid_for_win_rate"].get("50%"), "verdict": r["verdict"], "what to do": r["suggestion"],
        } for r in rows])
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            "win_rate": st.column_config.ProgressColumn("win rate", min_value=0, max_value=1, format="%.2f")})


def render_config(plan: CampaignPlan) -> None:
    cfg = plan.config
    st.subheader("Campaign config")
    st.markdown(f"Status: {badge(cfg.status)}", unsafe_allow_html=True)
    if cfg.pricing:
        pr = cfg.pricing
        st.markdown(f"**Pricing model: {pr.model}**  \n{pr.rationale}")
        if pr.cpc_bid:
            st.markdown(f"CPC range: ${pr.cpc_bid.low:.2f} to ${pr.cpc_bid.high:.2f} (mid ${pr.cpc_bid.mid:.2f}). Target CPA ${pr.target_cpa:.2f}. *tCPA unlock:* {pr.tcpa_unlock}")
    if cfg.budget:
        b = cfg.budget
        source = badge("assumed", "assumed") if b.budget_source != "form" else badge("from form", "ok")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Monthly budget", f"${b.monthly_budget_usd:,.0f}")
        c2.metric("Daily cap", f"${b.daily_cap_usd:,.0f}")
        c3.metric("Flight", f"{b.flight_start} to {b.flight_end}")
        c4.metric("Exploration reserve", f"${b.exploration_reserve_usd:,.0f}")
        st.markdown(f"Budget source: {source} &nbsp; Pacing: {html.escape(b.pacing)}", unsafe_allow_html=True)
        d1, d2, d3 = st.columns(3)
        d1.metric("Expected impressions", f"{b.expected_impressions:,}")
        d2.metric("Expected reach (unique shoppers)", f"{b.expected_reach:,}")
        d3.metric("Delivery goal", f"{b.delivery_goal}" + ("" if b.goal_feasible else " (not feasible)"))
        if b.allocations:
            df = pd.DataFrame([{
                "publisher": a.publisher_name, "share_pct": round(a.share * 100), "budget_usd": a.budget_usd, "cpc_bid_usd": a.cpc_bid_usd,
                "start_bid_usd": a.start_bid_usd, "impressions": a.expected_impressions,
                "clicks (mid)": a.expected_clicks.mid, "conversions (mid)": a.expected_conversions.mid,
                "host approval": "needs approval" if a.requires_host_approval else "", "rationale": a.rationale,
            } for a in b.allocations])
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                "share_pct": st.column_config.ProgressColumn("share", min_value=0, max_value=100, format="%d%%"),
            })
            st.bar_chart(df.set_index("publisher")["budget_usd"], color=ACCENT, horizontal=True)
        for w in b.warnings:
            st.warning(w)
    if cfg.bid_strategy:
        bs = cfg.bid_strategy
        st.markdown(f"**Bid strategy** (current phase: {badge(bs.current_phase)})  \n{html.escape(bs.rationale)}", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(bs.phases), hide_index=True, width="stretch")
        if bs.adjustments:
            st.dataframe(pd.DataFrame([a.model_dump() for a in bs.adjustments]), hide_index=True, width="stretch")
    if cfg.budget and cfg.budget.allocations:
        render_auction_insights(plan)
    for q in cfg.open_questions:
        st.markdown(f"- Open question: {q}")
    with st.expander("Full CampaignConfig JSON"):
        st.json(json.loads(cfg.model_dump_json()))
    st.download_button("Download CampaignConfig JSON", cfg.model_dump_json(indent=2), file_name="campaign_config.json", mime="application/json")


def render_footer(plan: CampaignPlan, model_label: str) -> None:
    timings = " | ".join(f"{k} {v:.1f}s" for k, v in plan.timings_s.items())
    st.caption(f"Stage timings: {timings or 'n/a'} | LLM: {model_label}")


def render_plan(plan: CampaignPlan, model_label: str) -> None:
    """All sections, in reading order."""
    render_gate_banner(plan)
    render_understanding(plan)
    render_publishers(plan)
    render_personas(plan)
    render_creatives(plan)
    render_config(plan)
    render_footer(plan, model_label)


# ------------------------------------------------------------------ shopper simulator

FEATURE_GROUPS = {
    "Name": ["name_valid", "display_name", "p_female", "age_bucket"],
    "Geography": ["state", "region", "timezone", "urbanicity", "urbanicity_proxy", "income_tier", "climate_band", "mountain",
                  "cold_or_mountain", "coast_distance_km", "is_coastal", "hurricane_coast", "sun_belt", "nearest_metro",
                  "metro_distance_km", "ski_distance_km", "near_ski_area", "delivery_speed_proxy"],
    "Time and calendar": ["local_hour", "day_of_week", "is_weekend", "daypart", "active_moments"],
    "Gift signal": ["gift_signal"],
    "Settlement and household (3rd-order, see source for the inference chain)": [
        "settlement_type", "p_has_kids", "p_homeowner", "life_stage", "car_dependent", "outdoor_lifestyle",
        "cold_season_now", "hot_season_now"],
}


def _format_feature_value(f) -> str:
    if f.distribution:
        top = sorted(f.distribution.items(), key=lambda kv: -kv[1])[:4]
        return ", ".join(f"{k} {v:.2f}" for k, v in top)
    return "none" if f.value is None else str(f.value)


def feature_rows(features) -> dict[str, list[dict]]:
    """Group ShopperFeatures into display rows. Unlisted Feature fields land in 'Other' automatically."""
    grouped: dict[str, list[dict]] = {g: [] for g in FEATURE_GROUPS}
    known = {n for names in FEATURE_GROUPS.values() for n in names}
    for name in type(features).model_fields:
        value = getattr(features, name)
        if name == "cart_prior" or not hasattr(value, "confidence"):
            continue
        group = next((g for g, names in FEATURE_GROUPS.items() if name in names), "Other" if name not in known else None)
        grouped.setdefault(group, []).append(
            {"feature": name, "value": _format_feature_value(value), "confidence": value.confidence, "source": value.source})
    cp = features.cart_prior
    grouped["Cart prior (from publisher)"] = [
        {"feature": "category", "value": cp.category or "none", "confidence": cp.confidence, "source": cp.source},
        {"feature": "subcategories", "value": ", ".join(cp.subcategories) or "none", "confidence": cp.confidence, "source": cp.source},
        {"feature": "avg_order_value_usd", "value": str(cp.avg_order_value_usd), "confidence": cp.confidence, "source": cp.source},
    ]
    return {g: rows for g, rows in grouped.items() if rows}


def render_features(features) -> None:
    for group, rows in feature_rows(features).items():
        st.markdown(f"**{group}**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1, format="%.2f")})


def load_holdout_plans() -> list[CampaignPlan]:
    """The 100 holdout brands' plans, used as competing advertisers in the simulator's auction."""
    folder = ROOT / "reports" / "holdout_plans"
    plans = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            plans.append(CampaignPlan.model_validate_json(path.read_text()))
        except Exception:  # an older plan shape shouldn't break the page
            continue
    return plans


def try_serve(signals, now_utc: datetime, plans: list[CampaignPlan], **kwargs):
    """Call the serving decision engine if it exists yet. Returns (decision, None) or (None, reason)."""
    try:
        from disco_planner.serving.decision import serve
    except ImportError:
        return None, "decision engine: coming in next build"
    try:
        return serve(signals, now_utc, plans, **kwargs), None
    except Exception as e:
        return None, f"decision engine raised {type(e).__name__}: {e}"


def render_decision(decision, plans: list[CampaignPlan]) -> None:
    """Render a ServeDecision defensively: its exact fields may still change, so unknown
    shapes fall back to JSON instead of crashing."""
    data = json.loads(decision.model_dump_json()) if hasattr(decision, "model_dump_json") else decision
    posterior = data.get("persona_posterior") or data.get("posterior")
    if isinstance(posterior, dict):
        st.markdown("**Persona posterior**")
        df = pd.DataFrame({"persona": list(posterior), "probability": list(posterior.values())}).sort_values("probability", ascending=False)
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            "probability": st.column_config.ProgressColumn("probability", min_value=0, max_value=1, format="%.2f")})
    reasons = data.get("reasons")
    if isinstance(reasons, list):
        st.markdown("**Reasons**")
        for r in reasons:
            st.markdown(f"- {r}")
    offers = data.get("offers") or []
    if offers:
        st.markdown("**Page auction: ranked by eCPM (final bid x expected CTR x 1000); each slot pays the second price**")
        st.dataframe(pd.DataFrame([{k: o.get(k) for k in ("slot", "campaign", "pricing_model", "bid", "price_paid", "expected_ctr", "ecpm",
                                                          "host_budget_left_usd", "host_revenue_per_1000", "bid_breakdown")}
                                   for o in offers]), hide_index=True, width="stretch")
    headline, body, cta = (data.get(k) for k in ("headline", "body", "cta"))
    if headline and body and cta:
        render_offer_card(headline, body, cta)
    with st.expander("Raw decision"):
        st.json(data)


# ------------------------------------------------------------------ evaluation page


def eval_reports() -> dict[str, Path]:
    return {p.stem.removeprefix("eval_"): p for p in sorted((ROOT / "reports").glob("eval_*.md"))}


def _table_to_df(lines: list[str]) -> pd.DataFrame:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    header, body = rows[0], rows[2:]  # rows[1] is the --- separator
    return pd.DataFrame([r + [""] * (len(header) - len(r)) for r in body], columns=header)


def split_markdown(md: str) -> list[tuple[str, object]]:
    """Split a markdown report into ('text', str) and ('table', DataFrame) chunks."""
    chunks: list[tuple[str, object]] = []
    buf: list[str] = []
    table: list[str] = []

    def flush_text():
        if buf and "\n".join(buf).strip():
            chunks.append(("text", "\n".join(buf)))
        buf.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            flush_text()
            table.append(line)
        else:
            if table:
                chunks.append(("table", _table_to_df(table)))
                table = []
            buf.append(line)
    if table:
        chunks.append(("table", _table_to_df(table)))
    flush_text()
    return chunks


def render_eval(path: Path) -> None:
    for kind, content in split_markdown(path.read_text()):
        if kind == "table":
            st.dataframe(content, hide_index=True, width="stretch")
        else:
            st.markdown(content)


# ------------------------------------------------------------------ how it works


def decision_sections() -> list[tuple[str, str, str]]:
    """(title, github anchor, body) for each '## ' heading in docs/DECISIONS.md."""
    text = (ROOT / "docs" / "DECISIONS.md").read_text()
    out = []
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = part.partition("\n")
        anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
        out.append((title.strip(), anchor, body.strip().rstrip("-").strip()))
    return out
