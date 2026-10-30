"""Economics, pricing model, bids and budget allocation (DECISIONS D2, D6).

Reasoning order, the way a careful advertiser would do it:
  what can I afford per customer -> target CPA -> max CPC per host -> how much
  budget do I need to learn anything -> split it across hosts I can actually win.
All priors come from config/pricing.yaml and are surfaced as assumptions.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from disco_planner.data import catalog
from disco_planner.domain.models import (
    AdvertiserInputs, AdvertiserProfile, Allocation, BudgetPlan, Economics, PricingPlan,
    PublisherScore, Range,
)


def _cfg() -> dict:
    return catalog.config("pricing")


def economics(profile: AdvertiserProfile, inputs: AdvertiserInputs, primary_category: str | None) -> Economics:
    eco = _cfg()["economics"]
    assumptions = []

    if inputs.price_usd:
        price, price_src = inputs.price_usd, "form"
    elif profile.price_point_usd:
        price, price_src = profile.price_point_usd, "stated"
    else:
        price, price_src = eco["default_price_by_tier"][profile.price_tier], "tier_default"
        assumptions.append(f"Price not given; assumed ${price:.0f} for a '{profile.price_tier}' product.")

    if inputs.gross_margin is not None:
        margin, margin_src = inputs.gross_margin, "form"
    else:
        margin = eco["default_gross_margin"].get(primary_category or "", eco["default_gross_margin"]["default"])
        margin_src = "category_default"
        assumptions.append(f"Gross margin not given; assumed {margin:.0%} (typical for {primary_category or 'DTC'}).")

    orders = eco["expected_orders"][profile.purchase_model]
    if profile.purchase_model in ("subscription", "repeat_consumable"):
        assumptions.append(f"{profile.purchase_model}: assumed {orders} orders per customer over ~6 months.")

    max_cac = price * margin * orders
    target = max_cac * eco["safety_factor"]
    assumptions.append(
        f"Target CPA = {eco['safety_factor']:.0%} of max affordable CAC "
        f"(${price:.0f} x {margin:.0%} margin x {orders} orders = ${max_cac:.2f})."
    )
    return Economics(
        price_usd=price, price_source=price_src, gross_margin=margin, margin_source=margin_src,
        expected_orders=orders, max_affordable_cac=round(max_cac, 2), target_cpa=round(target, 2),
        assumptions=assumptions,
    )


def _fit_multiplier(score: float, top: float) -> float:
    m = _cfg()["priors"]["fit_multiplier"]
    return m["min"] + (m["max"] - m["min"]) * (score / top if top else 0)


def _clamp_cpc(value: float) -> float:
    eco = _cfg()["economics"]
    return round(min(max(value, eco["cpc_floor"]), eco["cpc_ceiling"]), 2)


def pricing_plan(eco: Economics, inputs: AdvertiserInputs) -> PricingPlan:
    pri, pm = _cfg()["priors"], _cfg()["pricing_models"]
    cpc = Range(**{k: _clamp_cpc(eco.target_cpa * pri["cvr"][k]) for k in ("low", "mid", "high")})
    unlock = (
        f"tCPA unlocks after conversion tracking is verified and {pm['tcpa_requirements']['min_conversions']} "
        "click-through conversions are recorded; until then Disco can't predict conversion rate for this brand."
    )
    if inputs.objective == "awareness":
        cpm = round(cpc.mid * pri["ctr"]["mid"] * 1000, 2)
        return PricingPlan(
            model="CPM", cpm_bid=cpm, cpc_bid=cpc, target_cpa=eco.target_cpa, tcpa_unlock=unlock,
            rationale=f"Awareness goal: pay for reach. ${cpm} CPM is the CPC-equivalent at a {pri['ctr']['mid']:.1%} CTR, "
                      "so it costs the same as CPC if CTR matches our prior.",
        )
    return PricingPlan(
        model="CPC", cpc_bid=cpc, target_cpa=eco.target_cpa, tcpa_unlock=unlock,
        rationale=f"Acquisition goal with no conversion history: pay per click. Max CPC = target CPA ${eco.target_cpa:.2f} "
                  f"x click-to-purchase rate ({pri['cvr']['low']:.1%}-{pri['cvr']['high']:.1%}). "
                  "Clicks are observable without trusting a pixel, so both sides can verify billing.",
    )


def _approval_probability(s: PublisherScore, base: float) -> float:
    """P(host accepts our ad). Competitor hosts accept with `base`, interpolated by how sure
    intake was that it's a competitor (the agreement % is in the flag text)."""
    for flag in s.flags:
        if flag.startswith("competitor_conflict"):
            m = re.search(r"\((\d+)% of samples", flag)
            share = int(m.group(1)) / 100 if m else 1.0
            return round(1 - (1 - base) * share, 2)
    return 1.0


def budget_plan(
    scores: list[PublisherScore], eco: Economics, plan: PricingPlan, inputs: AdvertiserInputs,
    today: date, gifting_moment: str | None,
) -> BudgetPlan:
    cfg = _cfg()
    pri, bud = cfg["priors"], cfg["budget"]
    pubs = catalog.publisher_by_id()
    picks = [s for s in scores if s.recommended]
    explore = [s.publisher_name for s in scores if not s.recommended and "exploration candidate" in s.reason][:3]
    top = picks[0].score if picks else 1.0
    days = inputs.flight_days or bud["flight_days"]
    warnings: list[str] = []

    # Per-host bid, CTR, capacity and the minimum spend that teaches us anything.
    rows = []
    for s in picks:
        fit = _fit_multiplier(s.score, top)
        cpc = _clamp_cpc(eco.target_cpa * pri["cvr"]["mid"] * fit)
        ctr = pri["ctr"]["mid"] * fit
        approval = _approval_probability(s, pri["competitor_approval_probability"])
        impressions = pubs[s.publisher_id].monthly_impressions * pri["share_of_voice_cap"] * days / 30
        capacity = impressions * ctr * cpc * approval
        rows.append(dict(score=s, fit=fit, cpc=cpc, approval=approval, capacity=capacity,
                         min_viable=bud["min_clicks_per_publisher"] * cpc))

    learning_needed = sum(r["min_viable"] for r in rows) / (1 - bud["exploration_share"])
    if inputs.monthly_budget_usd:
        total, source = inputs.monthly_budget_usd, "form"
    else:
        total, source = round(learning_needed, -1), "recommended_learning_budget"
        warnings.append(f"No budget given: recommending ${total:,.0f}, the smallest budget that buys "
                        f"{bud['min_clicks_per_publisher']} clicks per host.")

    reserve = total * bud["exploration_share"] if explore else 0.0
    spendable = total - reserve

    # Concentrate: drop the weakest host until every remaining host gets its minimum.
    while rows and sum(r["min_viable"] for r in rows) > spendable:
        dropped = rows.pop()
        warnings.append(f"Budget too small for {dropped['score'].publisher_name}: concentrating on fewer hosts.")

    # Score-weighted split, capped by what each host can deliver; excess goes to uncapped hosts.
    budgets = {r["score"].publisher_id: 0.0 for r in rows}
    remaining, open_rows = spendable, list(rows)
    while remaining > 1 and open_rows:
        weight = sum(r["score"].score for r in open_rows)
        for r in list(open_rows):
            pid = r["score"].publisher_id
            add = remaining * r["score"].score / weight
            room = r["capacity"] - budgets[pid]
            budgets[pid] += min(add, room)
            if add >= room:
                open_rows.remove(r)
        remaining = spendable - sum(budgets.values())
    if remaining > 1:
        warnings.append(f"${remaining:,.0f} can't be spent on these hosts at the assumed share of voice; "
                        "lower the budget or add hosts.")

    allocations = []
    for r in rows:
        s, pid = r["score"], r["score"].publisher_id
        b = budgets[pid]
        clicks = b / r["cpc"]
        conv = {k: clicks * pri["cvr"][k] * r["fit"] for k in ("low", "mid", "high")}
        allocations.append(Allocation(
            publisher_id=pid, publisher_name=s.publisher_name, share=round(b / total, 3) if total else 0,
            budget_usd=round(b, 2), cpc_bid_usd=r["cpc"],
            start_bid_usd=_clamp_cpc(r["cpc"] * bud["start_bid_fraction"]),
            expected_clicks=Range(low=round(clicks * 0.8), mid=round(clicks), high=round(clicks * 1.2)),
            expected_conversions=Range(**{k: round(v, 1) for k, v in conv.items()}),
            capacity_usd=round(r["capacity"], 2), requires_host_approval=r["approval"] < 1,
            rationale=(f"score {s.score:.2f} -> fit x{r['fit']:.2f} on CTR/CVR; max bid ${r['cpc']:.2f} (break-even at "
                       f"target CPA), start at {bud['start_bid_fraction']:.0%} and raise only if under-delivering"
                       + ("; host sells a competing product, so it may decline (capacity discounted)" if r["approval"] < 1 else "")),
        ))

    pacing = "even daily pacing"
    if gifting_moment:
        pacing = f"front-loaded x{bud['gifting_front_load']} during '{gifting_moment}', even otherwise"
    stop = bud["stop_loss"]
    pacing += f"; pause a host if CPA > {stop['cpa_multiple']}x target after {stop['min_clicks']} clicks"

    return BudgetPlan(
        monthly_budget_usd=round(total, 2), budget_source=source, daily_cap_usd=round(total / days, 2),
        flight_start=today.isoformat(), flight_end=(today + timedelta(days=days)).isoformat(),
        pacing=pacing, learning_budget_needed_usd=round(learning_needed, 2),
        exploration_reserve_usd=round(reserve, 2), exploration_candidates=explore,
        allocations=allocations, warnings=warnings,
    )
