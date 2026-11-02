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
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # lets Streamlit Cloud import the package without installing it

from disco_planner.data import catalog  # noqa: E402
from disco_planner.domain.models import AdvertiserInputs  # noqa: E402
from disco_planner.llm.provider import LLMError, default_provider  # noqa: E402
from disco_planner.planning import explain, pipeline  # noqa: E402
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
            plan = pipeline.run(brief, provider, PLANNING_DATE, inputs, with_creatives=False)  # creatives follow (add_creatives)
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

def add_creatives_live(plan: CampaignPlan, live: bool) -> tuple[CampaignPlan, str | None, str]:
    """Second phase of a live run: write the creatives for a plan that is already on screen.
    Returns (plan, notice, call description); on failure the plan is returned unchanged with a notice."""
    from disco_planner.llm.provider import CACHE_ENABLED, CALL_STATS

    cache_token, stats_token = CACHE_ENABLED.set(not live), CALL_STATS.set({})
    try:
        provider = default_provider()
        return pipeline.add_creatives(plan, provider, PLANNING_DATE), None, describe_calls(provider, CALL_STATS.get())
    except Exception as e:
        return plan, f"Creatives failed ({type(e).__name__}: {str(e)[:160]}). The rest of the plan above is unaffected.", ""
    finally:
        CACHE_ENABLED.reset(cache_token)
        CALL_STATS.reset(stats_token)


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
    c1, c2, c3 = st.columns(3)
    c1.metric("Price tier", p.price_tier)
    c2.metric("Purchase model", p.purchase_model.replace("_", " "))
    c3.metric("Price point", f"${p.price_point_usd:,.0f}" if p.price_point_usd else "not stated")
    if p.stated_claims:
        st.markdown("**Claims the advertiser made** (the only claims creatives may use)")
        st.markdown(" ".join(badge(c) for c in p.stated_claims), unsafe_allow_html=True)
    if p.relevant_tags:
        st.markdown("**What hosts it relates to** (the product words we match against each host)")
        st.dataframe(
            pd.DataFrame([{"tag": explain.tags(t.tag), "weight": t.weight, "why": t.why} for t in sorted(p.relevant_tags, key=lambda t: -t.weight)]),
            hide_index=True, width="stretch",
            column_config={"weight": st.column_config.ProgressColumn("weight", min_value=0, max_value=1, format="%.2f")},
        )
    if p.competing_tags:
        st.markdown("**Competing products** (hosts that sell these may need to opt in)  \n" + " ".join(badge(explain.tags(t), "warn") for t in p.competing_tags), unsafe_allow_html=True)
    if p.missing_info:
        st.markdown("**Missing information**")
        for m in p.missing_info:
            st.markdown(f"- {m}")
    render_economics(plan)


def render_economics(plan: CampaignPlan) -> None:
    eco = plan.config.economics
    if not eco:
        return
    st.markdown("**Targets** <span class='muted'>only what you set; your margins are your business</span>", unsafe_allow_html=True)
    rows = [
        ("Target CPA", f"${eco.target_cpa:,.2f}" if eco.target_cpa else "none (maximise within budget)", eco.target_source),
        ("Price (used for ROAS only)", f"${eco.price_usd:,.2f}", eco.price_source),
    ]
    df = pd.DataFrame(rows, columns=["item", "value", "source"])
    df["status"] = df["source"].map(lambda s: "assumed" if s in ("tier_default", "category_default", "model") else "provided or derived")
    st.dataframe(df, hide_index=True, width="stretch")
    if eco.assumptions:
        st.markdown(" ".join(badge("assumed", "assumed") + " " + html.escape(a) + "<br>" for a in eco.assumptions), unsafe_allow_html=True)


def render_component_breakdown(score) -> None:
    """One bar per score component, in plain words. Product match and affordability multiply the score; the rest
    are averaged into "quality" with the weights shown."""
    for c in score.components:
        left, right = st.columns([2, 5])
        name = explain.NAMES.get(c.name, c.name.replace("_", " "))
        if c.value is None:  # not stated by the advertiser: left out of the score, not filled in
            left.markdown(f"**{name}**  \n<span class='muted'>not used</span>", unsafe_allow_html=True)
            right.markdown(f"<div class='muted'>{html.escape(explain.component(c))}</div>", unsafe_allow_html=True)
            continue
        role = "multiplies the score" if c.name in ("category_fit", "price_fit") else f"{c.weight:.0%} of quality"
        left.markdown(f"**{name}**  \n<span class='muted'>{c.value:.2f} · {role}</span>", unsafe_allow_html=True)
        right.markdown(bar(c.value) + f"<div class='muted'>{html.escape(explain.component(c))}</div>", unsafe_allow_html=True)


def usd(x: float) -> str:
    return f"${x:,.2f}"


def md(text: str) -> str:
    """Escape $ so Streamlit markdown shows money instead of typesetting '$0.57-$0.77' as a formula."""
    return text.replace("$", "\\$")


def flag_badges(flags: list[str]) -> str:
    return " ".join(badge(explain.badge(f), "warn") for f in flags)


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
            st.markdown(md(s.reason))
            render_component_breakdown(s)
    st.markdown(f"**Excluded and why** ({len(excluded)})")
    if excluded:
        st.markdown("\n".join(f"- **{html.escape(s.publisher_name)}** <span class='muted'>(fit {s.score:.2f})</span>: "
                              f"{md(s.reason.removeprefix('Excluded: '))}" for s in excluded), unsafe_allow_html=True)
        with st.expander("Score components for excluded publishers"):
            for s in excluded:
                st.markdown(f"**{s.publisher_name}**")
                render_component_breakdown(s)


def render_personas(plan: CampaignPlan) -> None:
    st.subheader("Personas")
    selected = [m for m in plan.personas if m.selected]
    rejected = [m for m in plan.personas if not m.selected]
    if not selected:
        st.info("No personas selected.")
    cols = st.columns(min(3, max(1, len(selected))))
    for i, m in enumerate(sorted(selected, key=lambda m: -m.score)):
        with cols[i % len(cols)].container(border=True):
            st.markdown(f"**{m.persona_name}**  \n<span class='muted'>match {m.score:.2f}</span>", unsafe_allow_html=True)
            st.markdown(bar(m.score), unsafe_allow_html=True)
            for r in m.reasons:
                st.markdown(f"- {r}")
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
    use_name = catalog.config("scoring")["creative"]["use_first_name"]
    mode = st.radio("Preview", ["With sample name", "Name unavailable"], horizontal=True, key="creative_mode") if use_name else "Name unavailable"
    sample = st.text_input("Sample name", "Sarah", key="creative_name") if mode == "With sample name" else None
    if use_name:
        st.caption("The stored creative keeps the `{first_name}` placeholder; the name is filled in at serve time, with the no-name headline as fallback.")
    reviews = {(r.persona_id, r.variant): r for r in plan.reviews}
    names = {m.persona_id: m.persona_name for m in plan.personas}
    by_persona: dict[str, list] = {}
    for v in plan.creatives:
        by_persona.setdefault(v.persona_id, []).append(v)
    for pid, variants in by_persona.items():
        st.markdown(f"**{names.get(pid, pid)}** <span class='muted'>{len(variants)} variants, split evenly at serving</span>", unsafe_allow_html=True)
        cols = st.columns(len(variants))
        for col, v in zip(cols, variants):
            with col:
                st.markdown(f"<span class='muted'>variant {v.variant} · angle: {html.escape(v.angle)}</span>", unsafe_allow_html=True)
                headline = render_name(v.headline, sample) if sample else v.headline_no_name
                render_offer_card(headline, v.body, v.cta)
                st.code(f"headline: {v.headline}\nno-name:  {v.headline_no_name}", language=None)
                r = reviews.get((v.persona_id, v.variant))
                if r:
                    verdict = badge("passed", "ok") if r.passed else badge("failed review", "bad")
                    claims = badge("claims ok", "ok") if r.claims_ok else badge("unsupported claims", "bad")
                    st.markdown(f"{verdict} {claims} {badge(f'persona fit {r.persona_fit}/5')}", unsafe_allow_html=True)
                    for issue in [*r.issues, *[f"Unsupported: {cl}" for cl in r.unsupported_claims]]:
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
            "your_max_bid": r["max_bid"], "rivals_here": r["competitors_on_host"],
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
        st.markdown(f"**Pricing: {pr.model} · bidding: {pr.bidding.replace('_', ' ')} · aggressiveness: {pr.aggressiveness}**  \n"
                    f"{md(html.escape(pr.rationale))}  \n<span class='muted'>{md(pr.tcpa_unlock)}</span>", unsafe_allow_html=True)
    if not cfg.budget:
        st.info("No budget set. Enter your budget in Campaign setup to see the allocation and the forecast; "
                "we don't suggest one.")
    if cfg.budget:
        b = cfg.budget
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Budget (your cap)", f"${b.monthly_budget_usd:,.0f}")
        c2.metric("Planned spend", f"${b.planned_spend_usd:,.0f}")
        c3.metric(f"Forecast {b.unit}", f"{b.forecast.mid:,.0f}", help=f"range {b.forecast.low:,.0f} to {b.forecast.high:,.0f}")
        c4.metric("Flight", f"{(date.fromisoformat(b.flight_end) - date.fromisoformat(b.flight_start)).days} days",
                  help=f"{b.flight_start} to {b.flight_end}")
        status_text = {"no_goal": "no goal set", "reachable": "goal reachable", "money_limited": "budget-limited",
                       "inventory_limited": "inventory-limited"}[b.goal_status]
        goal_txt = f"{b.goal_target:,} {b.unit}" if b.goal_target else "none"
        st.markdown(f"Goal: {goal_txt} · status: {badge(status_text, 'ok' if b.goal_status in ('reachable', 'no_goal') else 'assumed')} · "
                    f"binding constraint: **{b.binding_constraint}** · Pacing: {html.escape(b.pacing)}", unsafe_allow_html=True)
        for opt in b.replan_options:
            st.markdown(f"- {md(html.escape(opt))}")
        cap_cost = ""
        if b.forecast_without_share_cap and b.forecast_without_share_cap > b.forecast.mid * 1.001:
            cap_cost = (f" The {b.max_share_per_host:.0%} per-host cap costs ~{1 - b.forecast.mid / b.forecast_without_share_cap:.0%} "
                        f"of forecast {b.unit} ({b.forecast_without_share_cap:,.0f} without it) in exchange for spreading risk.")
        st.caption(md(f"At '{b.aggressiveness}' bidding these hosts offer up to {b.max_deliverable:,.0f} {b.unit} in the flight "
                   f"(~${b.spend_at_full_inventory_usd:,.0f}). Expected impressions {b.expected_impressions:,}, reach {b.expected_reach:,}." + cap_cost))
        if b.allocations:
            noun = {"conversions": "purchase", "clicks": "click", "impressions": "1,000 views", "reach": "1,000 people"}[b.objective]
            scale = 1000 if b.objective in ("impressions", "reach") else 1
            bids = {h.publisher_id: h for h in (cfg.bid_strategy.host_bids if cfg.bid_strategy else [])}
            rows = []
            for a in b.allocations:
                h, funded = bids.get(a.publisher_id), a.budget_usd > 0
                rows.append({
                    "host": a.publisher_name, "role": "learning test" if a.exploration else "main plan",
                    "budget": a.budget_usd, "share": round(a.share * 100),
                    "start bid": usd(a.cpc_bid_usd) if funded else "-", "max bid": usd(h.max_bid) if h else "-",
                    "page views won": a.win_share, "avg price paid": usd(a.avg_price_paid) if funded else "-",
                    f"expected {b.unit}": a.expected_units.mid,
                    "range": f"{a.expected_units.low:,.0f}-{a.expected_units.high:,.0f}" if funded else "",
                    f"cost per {noun}": usd(a.budget_usd / a.expected_units.mid * scale) if funded and a.expected_units.mid else "-",
                    "note": "confirm the host allows competing brands" if a.requires_host_approval else "",
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                "budget": st.column_config.NumberColumn(format="$%.0f"),
                "share": st.column_config.ProgressColumn("share", min_value=0, max_value=100, format="%d%%"),
                "page views won": st.column_config.NumberColumn(format="percent"),
                f"expected {b.unit}": st.column_config.NumberColumn(format="%.0f"),
            })
            floor = cfg.bid_strategy.host_bids[0].floor if cfg.bid_strategy and cfg.bid_strategy.host_bids else 0.05
            st.markdown("**Why each host gets this bid and budget**  \n" + "\n".join(
                f"- **{html.escape(a.publisher_name)}**: {md(a.bid_reason)}" for a in b.allocations if a.bid_reason))
            st.caption(md(f"**Start bid**: what we bid per click on day one: the price that wins this host's planned share of page views. "
                       f"**Max bid**: the most the bidder may go to on this host (your aggressiveness setting, never above your max CPC). "
                       f"Bids never go below ${floor:.2f}. **Avg price paid** is below the bid because the auction charges the "
                       f"second price. **Expected {b.unit}**: the likely value, with the range we expect."))
            with st.expander("Host-by-host detail (market price range, impressions, clicks)"):
                st.dataframe(pd.DataFrame([{
                    "host": a.publisher_name, "market price to win (p25-p75)": f"${a.market_price.low:.2f}-${a.market_price.high:.2f}",
                    "impressions": a.expected_impressions, "clicks": a.expected_clicks.mid, "detail": a.rationale,
                } for a in b.allocations]), hide_index=True, width="stretch")
        for w in b.warnings:
            st.warning(w)
    if cfg.bid_strategy:
        bs = cfg.bid_strategy
        st.markdown(f"**How bids change over the flight** (now: {badge(bs.current_phase.replace('_', ' '))})  \n{html.escape(bs.rationale)}",
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"phase": ph.get("name", "").replace("_", " "), "until": ph.get("until", ""),
                                    "what the bidder does": ph.get("rule", ""), "why": ph.get("why", "")} for ph in bs.phases]),
                     hide_index=True, width="stretch")
        if bs.adjustments:
            st.dataframe(pd.DataFrame([{"adjusts for": a.signal.replace("_", " "),
                                        "bid range": f"x{a.min_multiplier:.2f} to x{a.max_multiplier:.2f} of the start bid",
                                        "why": a.reason} for a in bs.adjustments]), hide_index=True, width="stretch")
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


def render_plan(plan: CampaignPlan, model_label: str, creatives_pending: bool = False) -> None:
    """All sections, in reading order. While the creatives are still being written their slot shows a placeholder."""
    render_gate_banner(plan)
    render_understanding(plan)
    render_publishers(plan)
    render_personas(plan)
    if creatives_pending:
        st.subheader("Creatives")
        st.info("Writing creatives... the rest of the plan below is already final.")
    else:
        render_creatives(plan)
    render_config(plan)
    render_footer(plan, model_label)


# ------------------------------------------------------------------ rival plans (auction insights)


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
