"""Economics, pricing model, bids, budget / delivery goals, and bid strategy (DECISIONS D2, D6).

Reasoning order, the way a careful advertiser would do it:
  1. what can I pay per customer?   target CPA, else target ROAS, else margin (last resort)
  2. what can I pay per click?      max CPC = target CPA x expected click->purchase rate
  3. what am I buying?              a budget, or an impressions / reach goal turned into one
  4. where?                         split across hosts I can actually win (capacity, approval)
  5. how is the price set?          bid strategy: fixed while learning, then adjusted per shopper
All priors come from config/pricing.yaml, each tagged [sourced] / [benchmark] / [assumed].
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from disco_planner.data import catalog
from disco_planner.domain.models import (
    AdvertiserInputs, AdvertiserProfile, Allocation, BidAdjustment, BidStrategy, BudgetPlan, Economics,
    HostBid, PricingPlan, PublisherScore, Range,
)


def _cfg() -> dict:
    return catalog.config("pricing")


# ------------------------------------------------------------------ 1. per-customer economics


def economics(profile: AdvertiserProfile, inputs: AdvertiserInputs, primary_category: str | None) -> Economics:
    """Target CPA from whatever the advertiser knows. Margin is only needed when they give
    neither a target CPA nor a target ROAS: it's what stops a bid from being arbitrary."""
    eco = _cfg()["economics"]
    assumptions: list[str] = []

    if inputs.price_usd:
        price, price_src = inputs.price_usd, "form"
    elif profile.price_point_usd:
        price, price_src = profile.price_point_usd, "stated"
    else:
        price, price_src = eco["default_price_by_tier"][profile.price_tier], "tier_default"
        assumptions.append(f"Price not given; assumed ${price:.0f} for a '{profile.price_tier}' product.")

    orders = eco["expected_orders"][profile.purchase_model]
    if profile.purchase_model in ("subscription", "repeat_consumable"):
        assumptions.append(f"{profile.purchase_model}: assumed {orders} orders per customer over ~6 months.")
    revenue_per_customer = price * orders

    margin, margin_src = None, "not_needed"
    if inputs.target_cpa_usd:
        target, cpa_src, max_cac = inputs.target_cpa_usd, "target_cpa", inputs.target_cpa_usd
        assumptions.append(f"Target CPA ${target:.2f} given by the advertiser.")
    elif inputs.target_roas:
        target, cpa_src = revenue_per_customer / inputs.target_roas, "target_roas"
        max_cac = target
        assumptions.append(f"Target CPA = revenue per customer ${revenue_per_customer:.0f} / target ROAS "
                           f"{inputs.target_roas:g} = ${target:.2f}.")
    else:
        if inputs.gross_margin is not None:
            margin, margin_src = inputs.gross_margin, "form"
        else:
            margin = eco["default_gross_margin"].get(primary_category or "", eco["default_gross_margin"]["default"])
            margin_src = "category_default"
            assumptions.append(f"No target CPA/ROAS or margin given; assumed {margin:.0%} gross margin "
                               f"(typical for {primary_category or 'DTC'}). Give a target CPA to replace this.")
        max_cac = revenue_per_customer * margin
        target, cpa_src = max_cac * eco["safety_factor"], "margin"
        assumptions.append(f"Target CPA = {eco['safety_factor']:.0%} of max affordable CAC (${price:.0f} x "
                           f"{margin:.0%} margin x {orders} orders = ${max_cac:.2f}).")
    return Economics(
        price_usd=price, price_source=price_src, gross_margin=margin, margin_source=margin_src, cpa_source=cpa_src,
        expected_orders=orders, max_affordable_cac=round(max_cac, 2), target_cpa=round(target, 2),
        assumptions=assumptions,
    )


# ------------------------------------------------------------------ 2. per-click price


def relative_fit(score: float, top: float) -> float:
    """CTR/CVR multiplier vs an AVERAGE host: best-fitting host x1.4, weakest pick x0.6.
    A ratio to the average, not a probability, so > 1 is expected."""
    m = _cfg()["priors"]["relative_fit_multiplier"]
    return m["min"] + (m["max"] - m["min"]) * (score / top if top else 0)


def category_cvr(category: str | None) -> dict[str, float]:
    pri = _cfg()["priors"]
    mult = pri["cvr_category_multiplier"].get(category or "", pri["cvr_category_multiplier"]["default"])
    return {k: v * mult for k, v in pri["cvr"].items()}


def _clamp_cpc(value: float) -> float:
    eco = _cfg()["economics"]
    return round(min(max(value, eco["cpc_floor"]), eco["cpc_ceiling"]), 2)


def pricing_plan(eco: Economics, inputs: AdvertiserInputs, category: str | None = None) -> PricingPlan:
    pri, pm = _cfg()["priors"], _cfg()["pricing_models"]
    cvr = category_cvr(category)
    cpc = Range(**{k: _clamp_cpc(eco.target_cpa * cvr[k]) for k in ("low", "mid", "high")})
    unlock = (
        f"tCPA unlocks after conversion tracking is verified and {pm['tcpa_requirements']['min_conversions']} "
        "click-through conversions are recorded; until then Disco can't predict conversion rate for this brand."
    )
    if inputs.objective == "awareness" or inputs.delivery_goal in ("impressions", "reach"):
        cpm = round(cpc.mid * pri["ctr"]["mid"] * 1000, 2)
        return PricingPlan(
            model="CPM", cpm_bid=cpm, cpc_bid=cpc, target_cpa=eco.target_cpa, tcpa_unlock=unlock,
            rationale=f"Reach/impression goal: pay for impressions. ${cpm} CPM is the CPC-equivalent at a "
                      f"{pri['ctr']['mid']:.1%} CTR, so it costs the same as CPC if CTR matches our prior.",
        )
    return PricingPlan(
        model="CPC", cpc_bid=cpc, target_cpa=eco.target_cpa, tcpa_unlock=unlock,
        rationale=f"Acquisition goal with no conversion history: pay per click. Max CPC = target CPA ${eco.target_cpa:.2f} "
                  f"x click-to-purchase rate ({cvr['low']:.1%}-{cvr['high']:.1%} for {category or 'this category'}). "
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


# ------------------------------------------------------------------ 3-4. budget / goal -> allocation


def _host_rows(picks: list[PublisherScore], eco: Economics, days: int, category: str | None) -> list[dict]:
    """Per host: fit, bid, CTR, CVR, cost per 1,000 impressions, and how much we can win."""
    pri, bud = _cfg()["priors"], _cfg()["budget"]
    pubs = catalog.publisher_by_id()
    cvr = category_cvr(category)
    top = picks[0].score if picks else 1.0
    rows = []
    for s in picks:
        fit = relative_fit(s.score, top)
        host_cvr = {k: v * fit for k, v in cvr.items()}
        cpc = _clamp_cpc(eco.target_cpa * host_cvr["mid"])
        ctr = pri["ctr"]["mid"] * fit
        approval = _approval_probability(s, pri["competitor_approval_probability"])
        impressions_cap = pubs[s.publisher_id].monthly_impressions * pri["share_of_voice_cap"] * days / 30 * approval
        cost_per_mille = cpc * ctr * 1000
        rows.append(dict(
            score=s, fit=fit, cpc=cpc, ctr=ctr, cvr=host_cvr, approval=approval, cpm=cost_per_mille,
            impressions_cap=impressions_cap, capacity=impressions_cap / 1000 * cost_per_mille,
            min_viable=bud["min_clicks_per_publisher"] * cpc,
        ))
    return rows


def _split(total: float, rows: list[dict], key: str) -> dict[str, float]:
    """Score-weighted split of `total`, each host capped at rows[key]; the excess flows to the rest."""
    alloc = {r["score"].publisher_id: 0.0 for r in rows}
    remaining, open_rows = total, list(rows)
    while remaining > 1e-6 and open_rows:
        weight = sum(r["score"].score for r in open_rows)
        for r in list(open_rows):
            pid = r["score"].publisher_id
            add = remaining * r["score"].score / weight
            room = r[key] - alloc[pid]
            alloc[pid] += min(add, room)
            if add >= room:
                open_rows.remove(r)
        remaining = total - sum(alloc.values())
    return alloc


def budget_plan(
    scores: list[PublisherScore], eco: Economics, plan: PricingPlan, inputs: AdvertiserInputs,
    today: date, gifting_moment: str | None, category: str | None = None,
) -> BudgetPlan:
    pri, bud = _cfg()["priors"], _cfg()["budget"]
    picks = [s for s in scores if s.recommended]
    explore = [s.publisher_name for s in scores if not s.recommended and "exploration candidate" in s.reason][:3]
    days = inputs.flight_days or bud["flight_days"]
    warnings: list[str] = []
    rows = _host_rows(picks, eco, days, category)
    learning_needed = sum(r["min_viable"] for r in rows) / (1 - bud["exploration_share"])
    feasible = True

    if inputs.delivery_goal in ("impressions", "reach") and (inputs.target_impressions or inputs.target_reach):
        # Goal -> impressions -> cost. Reach needs an assumed frequency (impressions per person).
        if inputs.delivery_goal == "reach":
            target_impr = (inputs.target_reach or 0) * pri["avg_frequency"]
            source = "reach_goal"
        else:
            target_impr, source = float(inputs.target_impressions or 0), "impression_goal"
        impressions = _split(target_impr, rows, "impressions_cap")
        reachable = sum(r["impressions_cap"] for r in rows)
        if target_impr > reachable + 1:
            feasible = False
            warnings.append(f"Goal needs {target_impr:,.0f} impressions but these hosts can deliver ~{reachable:,.0f} "
                            f"in {days} days at the assumed share of voice; extend the flight or add hosts.")
        budgets = {r["score"].publisher_id: impressions[r["score"].publisher_id] / 1000 * r["cpm"] for r in rows}
        reserve = 0.0
        total = sum(budgets.values())
    else:
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
        budgets = _split(spendable, rows, "capacity")
        unspent = spendable - sum(budgets.values())
        if unspent > 1:
            warnings.append(f"${unspent:,.0f} can't be spent on these hosts at the assumed share of voice; "
                            "lower the budget or add hosts.")

    allocations = []
    for r in rows:
        s, pid = r["score"], r["score"].publisher_id
        b = budgets[pid]
        impr = b / r["cpm"] * 1000 if r["cpm"] else 0
        clicks = impr * r["ctr"]
        conv = {k: clicks * r["cvr"][k] for k in ("low", "mid", "high")}
        allocations.append(Allocation(
            publisher_id=pid, publisher_name=s.publisher_name, share=round(b / total, 3) if total else 0,
            budget_usd=round(b, 2), cpc_bid_usd=r["cpc"],
            start_bid_usd=_clamp_cpc(r["cpc"] * bud["start_bid_fraction"]),
            expected_clicks=Range(low=round(clicks * 0.8), mid=round(clicks), high=round(clicks * 1.2)),
            expected_conversions=Range(**{k: round(v, 1) for k, v in conv.items()}),
            capacity_usd=round(r["capacity"], 2),
            expected_impressions=round(impr), expected_reach=round(impr / pri["avg_frequency"]),
            requires_host_approval=r["approval"] < 1,
            rationale=(f"score {s.score:.2f} -> relative fit x{r['fit']:.2f} on CTR/CVR; max bid ${r['cpc']:.2f} "
                       f"(break-even at target CPA), start at {bud['start_bid_fraction']:.0%}"
                       + ("; host sells a competing product, so it may decline (capacity discounted)" if r["approval"] < 1 else "")),
        ))

    pacing = "even daily pacing"
    if gifting_moment:
        pacing = f"front-loaded x{bud['gifting_front_load']} during '{gifting_moment}', even otherwise"
    stop = bud["stop_loss"]
    pacing += f"; pause a host if CPA > {stop['cpa_multiple']}x target after {stop['min_clicks']} clicks"
    total_impr = sum(a.expected_impressions for a in allocations)

    return BudgetPlan(
        monthly_budget_usd=round(total, 2), budget_source=source, delivery_goal=inputs.delivery_goal,
        daily_cap_usd=round(total / days, 2),
        flight_start=today.isoformat(), flight_end=(today + timedelta(days=days)).isoformat(),
        pacing=pacing, learning_budget_needed_usd=round(learning_needed, 2),
        exploration_reserve_usd=round(reserve, 2), exploration_candidates=explore,
        expected_impressions=total_impr, expected_reach=round(total_impr / pri["avg_frequency"]),
        goal_feasible=feasible, allocations=allocations, warnings=warnings,
    )


# ------------------------------------------------------------------ 5. bid strategy


def bid_strategy(plan: PricingPlan, budget: BudgetPlan) -> BidStrategy:
    """Phased: stable bids while we learn real CTR/CVR, then per-shopper adjustments, then tCPA.
    Adjusting bids before anything is measured would only add noise to what we're measuring."""
    cfg = _cfg()["bid_strategy"]
    floor = _cfg()["economics"]["cpc_floor"]
    learn = cfg["learning_phase"]
    adj = cfg["adjustments"]
    adjustments = [
        BidAdjustment(signal="conversion_likelihood", min_multiplier=adj["conversion_likelihood"]["min"],
                      max_multiplier=adj["conversion_likelihood"]["max"],
                      reason="Bid higher when the shopper looks like a high converter: their persona match for this "
                             "campaign vs the host's average shopper (from the serve-time persona posterior)."),
        BidAdjustment(signal="pacing", min_multiplier=adj["pacing"]["min"], max_multiplier=adj["pacing"]["max"],
                      reason="Under-delivering vs the daily plan -> bid up a little; over-delivering -> bid down."),
    ]
    if plan.model == "CPM":
        return BidStrategy(
            current_phase="fixed_cpm", host_bids=[], adjustments=adjustments[1:],
            phases=[{"name": "fixed_cpm", "rule": f"bid ${plan.cpm_bid} CPM; pacing adjustment only"}],
            rationale="Reach goal: the advertiser is buying impressions, so per-shopper conversion adjustments "
                      "don't apply; only pacing moves the bid.",
        )
    return BidStrategy(
        current_phase="learning",
        host_bids=[HostBid(publisher_id=a.publisher_id, start_bid=a.start_bid_usd or a.cpc_bid_usd,
                           max_bid=a.cpc_bid_usd, floor=floor) for a in budget.allocations],
        adjustments=adjustments,
        phases=[
            {"name": "learning", "until": f"{learn['days']} days or {learn['min_clicks_per_host']} clicks per host",
             "rule": "bid = start bid per host (70% of break-even); pacing adjustment only",
             "why": "stable bids so measured CTR/CVR aren't confounded by bid changes"},
            {"name": "enhanced_cpc", "until": plan.tcpa_unlock,
             "rule": "bid = start bid x conversion_likelihood x pacing, clamped to [floor, max bid]",
             "why": "spend more on shoppers who look likely to buy, never above break-even"},
            {"name": "target_cpa", "rule": f"Disco bids automatically to hit ${plan.target_cpa:.2f} CPA",
             "why": "only once Disco can predict this brand's conversion rate"},
        ],
        rationale="Start below break-even and fixed so the first two weeks measure the hosts, not the bidder; "
                  "then bid per shopper within a hard ceiling (max CPC = target CPA x CVR), so a single "
                  "click can never cost more than it's expected to earn.",
    )
