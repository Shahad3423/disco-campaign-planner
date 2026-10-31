"""Economics, bids, budget allocation, forecast and bid strategy (DECISIONS D2, D6, D15, D23).

The advertiser sets the budget (a hard cap; we never suggest one), an objective (conversions, clicks,
impressions or reach), optionally a goal number, and a bidding mode (maximise within budget, or a
target CPA / ROAS). We then:
  1. economics    break-even per customer as GUIDANCE; a target exists only if the advertiser set one
  2. bids         a hard max bid per click (advertiser's own, or a labelled category default)
  3. per host     expected price, CTR, CVR, inventory, and a diminishing-returns response curve
  4. allocation   learning floor on every host first, then water-filling: each next dollar goes to the
                  host buying the most objective units per dollar (stops at the goal if asked)
  5. forecast     what the planned spend buys (a range), and whether the goal is reachable,
                  money-limited or inventory-limited, with concrete replan options
  6. bid strategy learning (fixed) -> maximise / enhanced (adjusted per shopper, capped) -> tCPA
All priors come from config/pricing.yaml, each tagged [sourced] / [benchmark] / [assumed].
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from disco_planner.data import catalog
from disco_planner.domain.models import (
    OBJECTIVE_UNIT, AdvertiserInputs, AdvertiserProfile, Allocation, BidAdjustment, BidStrategy, BudgetPlan,
    Economics, HostBid, PricingPlan, PublisherScore, Range,
)


def _cfg() -> dict:
    return catalog.config("pricing")


# ------------------------------------------------------------------ 1. economics (guidance + targets)


def economics(profile: AdvertiserProfile, inputs: AdvertiserInputs, primary_category: str | None) -> Economics:
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

    if inputs.gross_margin is not None:
        margin, margin_src = inputs.gross_margin, "form"
    else:
        margin = eco["default_gross_margin"].get(primary_category or "", eco["default_gross_margin"]["default"])
        margin_src = "category_default"
        assumptions.append(f"Margin not given; assumed {margin:.0%} (typical for {primary_category or 'DTC'}) "
                           "for the break-even estimate only.")
    break_even = price * margin * orders
    assumptions.append(f"Break-even per customer (guidance, not enforced): ${price:.0f} x {margin:.0%} x {orders} "
                       f"orders = ${break_even:.2f}.")

    target, source = None, "none"
    if inputs.bidding == "target_cpa" and inputs.target_cpa_usd:
        target, source = inputs.target_cpa_usd, "target_cpa"
    elif inputs.bidding == "target_roas" and inputs.target_roas:
        target, source = price * orders / inputs.target_roas, "target_roas"
        assumptions.append(f"Target CPA from ROAS: revenue per customer ${price * orders:.0f} / {inputs.target_roas:g} "
                           f"= ${target:.2f}.")
    return Economics(price_usd=price, price_source=price_src, expected_orders=orders, gross_margin=margin,
                     margin_source=margin_src, break_even_cac=round(break_even, 2),
                     target_cpa=round(target, 2) if target else None, target_source=source, assumptions=assumptions)


# ------------------------------------------------------------------ 2. bids and caps


def relative_fit(score: float, top: float) -> float:
    """CTR/CVR multiplier vs an AVERAGE host: best-fitting host x1.4, weakest pick x0.6 (a ratio, so > 1 is fine)."""
    m = _cfg()["priors"]["relative_fit_multiplier"]
    return m["min"] + (m["max"] - m["min"]) * (score / top if top else 0)


def category_cvr(category: str | None) -> dict[str, float]:
    pri = _cfg()["priors"]
    mult = pri["cvr_category_multiplier"].get(category or "", pri["cvr_category_multiplier"]["default"])
    return {k: v * mult for k, v in pri["cvr"].items()}


def _cap(inputs: AdvertiserInputs, category: str | None) -> tuple[float, str]:
    if inputs.max_cpc_usd:
        return inputs.max_cpc_usd, "advertiser"
    table = _cfg()["default_max_cpc_by_category"]
    return table.get(category or "", table["default"]), "category_default"


def pricing_plan(eco: Economics, inputs: AdvertiserInputs, category: str | None = None) -> PricingPlan:
    pri, pm, bud = _cfg()["priors"], _cfg()["pricing_models"], _cfg()["budget"]
    cvr = category_cvr(category)
    cap, cap_src = _cap(inputs, category)
    floor = _cfg()["economics"]["cpc_floor"]
    clamp = lambda x: round(min(max(x, floor), cap), 2)
    break_even_cpc = round(eco.break_even_cac * cvr["mid"], 2)
    unlock = (f"tCPA unlocks after conversion tracking is verified and {pm['tcpa_requirements']['min_conversions']} "
              "click-through conversions are recorded.")
    if eco.target_cpa:
        cpc = Range(**{k: clamp(eco.target_cpa * cvr[k]) for k in ("low", "mid", "high")})
        why = (f"Bid toward target CPA ${eco.target_cpa:.2f}: the click price that hits it is target x conversion rate "
               f"({cvr['low']:.1%}-{cvr['high']:.1%}); hard cap ${cap:.2f} ({cap_src.replace('_', ' ')}).")
    else:
        cpc = Range(low=clamp(cap * 0.5), mid=clamp(cap * bud["start_bid_fraction"]), high=cap)
        why = (f"Maximise {OBJECTIVE_UNIT[inputs.objective]} within the budget: bids move between ${floor:.2f} and the "
               f"${cap:.2f} cap ({cap_src.replace('_', ' ')}) to spend it well; no CPA target is invented.")
    if cap > break_even_cpc:
        why += (f" Guidance: at the assumed margin, a click is worth about ${break_even_cpc:.2f}; bidding above that "
                "loses money per customer. Set a target CPA or a lower max CPC to enforce it.")
    if inputs.objective in ("impressions", "reach"):
        cpm = round(cpc.mid * pri["ctr"]["mid"] * 1000, 2)
        return PricingPlan(model="CPM", bidding=inputs.bidding, max_cpc_cap=cap, cap_source=cap_src, cpc_range=cpc,
                           cpm_bid=cpm, target_cpa=eco.target_cpa, break_even_cpc=break_even_cpc, tcpa_unlock=unlock,
                           rationale=f"{inputs.objective.capitalize()} objective: pay per 1,000 views; ${cpm} CPM is the "
                                     f"click-equivalent of a ${cpc.mid:.2f} bid at a {pri['ctr']['mid']:.1%} CTR. " + why)
    return PricingPlan(model="CPC", bidding=inputs.bidding, max_cpc_cap=cap, cap_source=cap_src, cpc_range=cpc,
                       target_cpa=eco.target_cpa, break_even_cpc=break_even_cpc, tcpa_unlock=unlock,
                       rationale="Pay per click: verifiable without trusting a pixel. " + why)


# ------------------------------------------------------------------ 3. per-host economics and response curves


def host_rows(scores: list[PublisherScore], eco: Economics, pricing: PricingPlan, inputs: AdvertiserInputs,
              days: int, category: str | None) -> list[dict]:
    pri, bud = _cfg()["priors"], _cfg()["budget"]
    pubs = catalog.publisher_by_id()
    picks = [s for s in scores if s.recommended]
    top = picks[0].score if picks else 1.0
    cvr_all = category_cvr(category)
    cap, floor = pricing.max_cpc_cap, _cfg()["economics"]["cpc_floor"]
    best_cvr = cvr_all["mid"] * relative_fit(top, top)
    rows = []
    for s in picks:
        fit = relative_fit(s.score, top)
        cvr = {k: v * fit for k, v in cvr_all.items()}
        ctr = pri["ctr"]["mid"] * fit
        if eco.target_cpa:                       # bid that hits the target on average, capped
            max_bid = min(cap, max(floor, eco.target_cpa * cvr["mid"]))
        elif inputs.objective == "conversions":  # maximise conversions: bid in proportion to expected CVR
            max_bid = max(floor, cap * cvr["mid"] / best_cvr)
        else:                                     # clicks / impressions / reach: same cap everywhere
            max_bid = cap
        start = max(floor, round(max_bid * bud["start_bid_fraction"], 2))
        cpm = pricing.cpm_bid if pricing.model == "CPM" else start * ctr * 1000
        impressions_cap = pubs[s.publisher_id].monthly_impressions * pri["share_of_voice_cap"] * days / 30
        spend_cap = impressions_cap / 1000 * cpm
        per_dollar = {  # objective units per dollar at the start of the curve
            "conversions": ctr * cvr["mid"] * 1000 / cpm,
            "clicks": ctr * 1000 / cpm,
            "impressions": 1000 / cpm,
            "reach": 1000 / (cpm * pri["avg_frequency"]),
        }[inputs.objective]
        rows.append(dict(score=s, fit=fit, cvr=cvr, ctr=ctr, max_bid=round(max_bid, 2), start=start, cpm=cpm,
                         impressions_cap=impressions_cap, spend_cap=spend_cap, inventory_cap=spend_cap, rate=per_dollar,
                         floor=min(spend_cap, bud["min_clicks_per_publisher"] * start),
                         competitor=any(f.startswith("competitor_conflict") for f in s.flags)))
    return rows


def _units(row: dict, spend: float) -> float:
    """Diminishing returns: rate x S x (1 - exp(-c s / S)) / c. S = the host's INVENTORY in dollars
    (saturation is about how much of its audience we've bought), not any budget cap."""
    c, S = _cfg()["allocation"]["curvature"], row["inventory_cap"]
    if S <= 0:
        return 0.0
    return row["rate"] * S * (1 - math.exp(-c * min(spend, S) / S)) / c


def _marginal(row: dict, spend: float) -> float:
    """Marginal units per dollar; 0 once the host hits its hard spend cap (inventory or diversification)."""
    c, S = _cfg()["allocation"]["curvature"], row["inventory_cap"]
    return 0.0 if spend >= row["spend_cap"] or S <= 0 else row["rate"] * math.exp(-c * spend / S)


def _water_fill(rows: list[dict], start: dict[str, float], budget: float, goal: float | None) -> dict[str, float]:
    """Greedy on concave curves (optimal for separable concave objectives): each step goes to the host
    with the highest marginal units per dollar; stops at the budget, at full inventory, or at the goal."""
    spend = dict(start)
    step = max(budget / _cfg()["allocation"]["steps"], 1.0)
    remaining = budget - sum(spend.values())
    while remaining > 1e-6:
        if goal is not None and sum(_units(r, spend[r["score"].publisher_id]) for r in rows) >= goal:
            break
        best = max(rows, key=lambda r: _marginal(r, spend[r["score"].publisher_id]), default=None)
        if best is None or _marginal(best, spend[best["score"].publisher_id]) <= 0:
            break  # every host at capacity
        pid = best["score"].publisher_id
        add = min(step, remaining, best["spend_cap"] - spend[pid])
        spend[pid] += add
        remaining -= add
    return spend


def _cost_to_goal(rows: list[dict], floors: dict, goal: float, spend_full: float) -> float:
    """Spend needed to reach the goal with true inventory (the share cap scales with a bigger budget)."""
    sim = [{**r, "spend_cap": r["inventory_cap"]} for r in rows]
    return round(sum(_water_fill(sim, floors, spend_full + 1, goal).values()), 2)


# ------------------------------------------------------------------ 4-5. allocation + forecast


def budget_plan(
    scores: list[PublisherScore], eco: Economics, pricing: PricingPlan, inputs: AdvertiserInputs,
    today: date, gifting_moment: str | None, category: str | None = None,
) -> BudgetPlan | None:
    if not inputs.monthly_budget_usd:
        return None  # the advertiser chooses the budget; without it there is nothing to allocate
    cfg = _cfg()
    pri, bud = cfg["priors"], cfg["budget"]
    budget = inputs.monthly_budget_usd
    days = inputs.flight_days or bud["flight_days"]
    unit = OBJECTIVE_UNIT[inputs.objective]
    goal = float(inputs.goal_target) if inputs.goal_target else None
    explore = [s.publisher_name for s in scores if not s.recommended and "exploration candidate" in s.reason][:3]
    rows = host_rows(scores, eco, pricing, inputs, days, category)
    warnings: list[str] = []

    learning_floor = sum(r["floor"] for r in rows)
    stop_at_goal = goal is not None and inputs.when_goal_met == "stop"
    reserve = budget * bud["exploration_share"] if explore and not stop_at_goal else 0.0
    spendable = budget - reserve
    while rows and sum(r["floor"] for r in rows) > spendable:
        dropped = rows.pop()  # weakest first: concentrate rather than learn nothing everywhere
        warnings.append(f"Budget can't measure every host: dropped {dropped['score'].publisher_name} "
                        f"(needs ~${dropped['floor']:,.0f} to learn).")

    floors = {r["score"].publisher_id: r["floor"] for r in rows}
    # diversification: cap each host's spend at a share of the budget (water-filling respects spend_cap)
    share_cap = max(cfg["allocation"]["max_share_per_host"], 1 / max(1, len(rows)))
    for r in rows:
        r["spend_cap"] = min(r["inventory_cap"], share_cap * spendable)
    spend = _water_fill(rows, floors, spendable, goal if stop_at_goal else None)
    units_mid = sum(_units(r, spend[r["score"].publisher_id]) for r in rows)
    lo, hi = cfg["allocation"]["forecast_spread"][inputs.objective]
    full = {r["score"].publisher_id: r["inventory_cap"] for r in rows}
    max_deliverable = sum(_units(r, full[r["score"].publisher_id]) for r in rows)
    spend_full = sum(full.values())
    host_spend = sum(spend.values())
    all_capped = all(spend[r["score"].publisher_id] >= r["spend_cap"] - 1 for r in rows) if rows else True
    inventory_bound = all(spend[r["score"].publisher_id] >= r["inventory_cap"] - 1 for r in rows) if rows else True

    status, binding, to_goal, options = "no_goal", ("inventory" if inventory_bound else "budget"), None, []
    if goal is not None:
        if units_mid >= goal * 0.999:
            status = "reachable"
            binding = "goal" if stop_at_goal else binding
            to_goal = round(sum(spend.values()), 2) if stop_at_goal else _cost_to_goal(rows, floors, goal, spend_full)
            if stop_at_goal and host_spend < spendable - 1:
                options.append(f"Goal reached with ~${host_spend:,.0f} of your ${budget:,.0f}; the rest stays unspent "
                               "(switch to 'keep spending' to buy more).")
        elif max_deliverable >= goal:
            status, binding = "money_limited", "budget"
            to_goal = _cost_to_goal(rows, floors, goal, spend_full)
            options += [f"Raise the budget to ~${to_goal + reserve:,.0f} to reach {goal:,.0f} {unit}.",
                        f"Or accept ~{units_mid:,.0f} {unit} at ${budget:,.0f}."]
        else:
            status, binding = "inventory_limited", "inventory"
            need_days = math.ceil(days * goal / max(max_deliverable, 1e-9))
            options += [f"These hosts can deliver only ~{max_deliverable:,.0f} {unit} in {days} days, whatever the budget.",
                        f"Extend the flight to ~{need_days} days, or add hosts (candidates: {', '.join(explore) or 'none'}).",
                        f"Or accept ~{max_deliverable:,.0f} {unit} (spend ~${spend_full:,.0f})."]
    elif all_capped and host_spend < spendable - 1:
        why = ("these hosts' inventory" if inventory_bound
               else f"the {cfg['allocation']['max_share_per_host']:.0%} per-host diversification cap")
        warnings.append(f"Only ~${host_spend:,.0f} can be spent given {why}; add hosts or lower the budget.")

    planned = host_spend + reserve
    allocations = []
    for r in rows:
        s, pid = r["score"], r["score"].publisher_id
        b = spend[pid]
        impressions = b / r["cpm"] * 1000 if r["cpm"] else 0
        clicks = impressions * r["ctr"]
        units = _units(r, b)
        allocations.append(Allocation(
            publisher_id=pid, publisher_name=s.publisher_name, share=round(b / planned, 3) if planned else 0,
            budget_usd=round(b, 2), cpc_bid_usd=r["max_bid"], start_bid_usd=r["start"],
            expected_clicks=Range(low=round(clicks * 0.8), mid=round(clicks), high=round(clicks * 1.2)),
            expected_conversions=Range(**{k: round(clicks * r["cvr"][k], 1) for k in ("low", "mid", "high")}),
            expected_units=Range(low=round(units * lo, 1), mid=round(units, 1), high=round(units * hi, 1)),
            capacity_usd=round(r["spend_cap"], 2), expected_impressions=round(impressions),
            expected_reach=round(impressions / pri["avg_frequency"]), requires_host_approval=r["competitor"],
            rationale=(f"score {s.score:.2f}, relative fit x{r['fit']:.2f}; {r['rate']:.4f} {unit}/$ at the start, "
                       f"falling as the host saturates; bid ${r['start']:.2f} (cap ${r['max_bid']:.2f})"
                       + ("; host also sells this kind of product: confirm it allows competing brands, otherwise "
                          "this budget reflows to the other hosts" if r["competitor"] else "")),
        ))

    pacing = "even daily pacing"
    if gifting_moment:
        pacing = f"front-loaded x{bud['gifting_front_load']} during '{gifting_moment}', even otherwise"
    if stop_at_goal:
        pacing += "; stop when the goal is reached"
    if eco.target_cpa:
        stop = bud["stop_loss"]
        pacing += f"; pause a host if CPA > {stop['cpa_multiple']}x target after {stop['min_clicks']} clicks"
    total_impr = sum(a.expected_impressions for a in allocations)
    return BudgetPlan(
        monthly_budget_usd=round(budget, 2), planned_spend_usd=round(planned, 2), objective=inputs.objective, unit=unit,
        goal_target=inputs.goal_target, when_goal_met=inputs.when_goal_met,
        forecast=Range(low=round(units_mid * lo, 1), mid=round(units_mid, 1), high=round(units_mid * hi, 1)),
        goal_status=status, binding_constraint=binding, spend_to_reach_goal_usd=to_goal,
        max_deliverable=round(max_deliverable, 1), spend_at_full_inventory_usd=round(spend_full, 2),
        replan_options=options, expected_impressions=total_impr, expected_reach=round(total_impr / pri["avg_frequency"]),
        daily_cap_usd=round(budget / days, 2), flight_start=today.isoformat(),
        flight_end=(today + timedelta(days=days)).isoformat(), pacing=pacing,
        learning_floor_usd=round(learning_floor, 2), exploration_reserve_usd=round(reserve, 2),
        exploration_candidates=explore, allocations=allocations, warnings=warnings,
    )


# ------------------------------------------------------------------ 6. bid strategy


def bid_strategy(pricing: PricingPlan, rows: list[dict], inputs: AdvertiserInputs) -> BidStrategy:
    cfg = _cfg()["bid_strategy"]
    floor = _cfg()["economics"]["cpc_floor"]
    learn, adj = cfg["learning_phase"], cfg["adjustments"]
    pacing = BidAdjustment(signal="pacing", min_multiplier=adj["pacing"]["min"], max_multiplier=adj["pacing"]["max"],
                           reason="Under-delivering vs the daily plan -> bid up a little; over-delivering -> bid down.")
    conv = BidAdjustment(signal="conversion_likelihood", min_multiplier=adj["conversion_likelihood"]["min"],
                         max_multiplier=adj["conversion_likelihood"]["max"],
                         reason="Bid higher when the shopper looks like a likely converter (persona match vs the host's average shopper).")
    learning = {"name": "learning", "until": f"{learn['days']} days or {learn['min_clicks_per_host']} clicks per host",
                "rule": "fixed start bid per host; pacing only", "why": "stable bids so measured CTR/CVR aren't confounded"}
    host_bids = [HostBid(publisher_id=r["score"].publisher_id, start_bid=r["start"], max_bid=r["max_bid"], floor=floor)
                 for r in rows]
    if pricing.model == "CPM":
        return BidStrategy(current_phase="fixed_cpm", host_bids=host_bids, adjustments=[pacing],
                           phases=[{"name": "fixed_cpm", "rule": f"${pricing.cpm_bid} CPM; pacing only"}],
                           rationale="Buying views: per-shopper conversion adjustments don't apply; pacing keeps delivery on plan.")
    if inputs.bidding == "maximize":
        rule = ("bid = start x pacing" + (" x conversion likelihood" if inputs.objective == "conversions" else "")
                + ", within [floor, cap]: spend the budget on the best opportunities")
        return BidStrategy(
            current_phase="learning", host_bids=host_bids,
            adjustments=[pacing] + ([conv] if inputs.objective == "conversions" else []),
            phases=[learning, {"name": "maximize", "rule": rule,
                               "why": "no CPA target was set, so the goal is the most units for the budget"}],
            rationale=f"Maximise {OBJECTIVE_UNIT[inputs.objective]} within the budget, never above the "
                      f"${pricing.max_cpc_cap:.2f} cap ({pricing.cap_source.replace('_', ' ')}).",
        )
    return BidStrategy(
        current_phase="learning", host_bids=host_bids, adjustments=[pacing, conv],
        phases=[learning,
                {"name": "enhanced_cpc", "until": pricing.tcpa_unlock,
                 "rule": "bid = start x conversion likelihood x pacing, within [floor, cap]",
                 "why": "aim at the target on average while learning who converts"},
                {"name": "target_cpa", "rule": f"Disco bids automatically toward ${pricing.target_cpa:.2f} average CPA",
                 "why": "only once Disco can predict this brand's conversion rate"}],
        rationale=f"Target ${pricing.target_cpa:.2f} CPA as an average (a goal for the bidder, not a per-click limit); "
                  f"the hard limit is the ${pricing.max_cpc_cap:.2f} cap.",
    )
