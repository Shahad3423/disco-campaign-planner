"""Economics, market prices, allocation, forecast and bid strategy (DECISIONS D2, D15, D23, D24).

Disco's job: deliver the advertiser's objective within their budget, at market prices. The advertiser
owns everything about their own economics (margins, what a customer is worth); they express it only
through their budget, objective, optional goal, optional target CPA / ROAS and optional max CPC.

  1. economics   a target exists only if the advertiser set one
  2. market      per host, the bid landscape: price to win each page view vs the other campaigns
  3. allocation  buy the cheapest objective units first across hosts (greedy on rising unit costs),
                 until the budget, the goal, the aggressiveness limit, the share cap or a price cap stops it
  4. forecast    what the plan buys (a range) and whether the goal is reachable, budget-limited or
                 inventory-limited, with concrete replan options
  5. bids        per host: the planned bid (the price that wins the planned share) and its ceiling
"""
from __future__ import annotations

import heapq
import math
from datetime import date, timedelta

from disco_planner.data import catalog
from disco_planner.domain.models import (
    OBJECTIVE_UNIT, AdvertiserInputs, AdvertiserProfile, Allocation, BidAdjustment, BidStrategy, BudgetPlan,
    Economics, HostBid, PricingPlan, PublisherScore, Range,
)
from disco_planner.planning import market


def _cfg() -> dict:
    return catalog.config("pricing")


# ------------------------------------------------------------------ 1. economics (the advertiser's targets only)


def economics(profile: AdvertiserProfile, inputs: AdvertiserInputs, primary_category: str | None) -> Economics:
    eco = _cfg()["economics"]
    assumptions: list[str] = []
    if inputs.price_usd:
        price, price_src = inputs.price_usd, "form"
    elif profile.price_point_usd:
        price, price_src = profile.price_point_usd, "stated"
    else:
        price, price_src = eco["default_price_by_tier"][profile.price_tier], "tier_default"
    orders = eco["expected_orders"][profile.purchase_model]
    target, source = None, "none"
    if inputs.bidding == "target_cpa" and inputs.target_cpa_usd:
        target, source = inputs.target_cpa_usd, "target_cpa"
    elif inputs.bidding == "target_roas" and inputs.target_roas:
        target, source = price * orders / inputs.target_roas, "target_roas"
        if price_src == "tier_default":
            assumptions.append(f"Price not given; assumed ${price:.0f} ('{profile.price_tier}') to turn ROAS into a CPA.")
        assumptions.append(f"Target CPA from ROAS: revenue per customer ${price * orders:.0f} "
                           f"(${price:.0f} x {orders} orders) / {inputs.target_roas:g} = ${target:.2f}.")
    return Economics(price_usd=price, price_source=price_src, expected_orders=orders,
                     target_cpa=round(target, 2) if target else None, target_source=source, assumptions=assumptions)


def category_cvr(category: str | None) -> dict[str, float]:
    pri = _cfg()["priors"]
    mult = pri["cvr_category_multiplier"].get(category or "", pri["cvr_category_multiplier"]["default"])
    return {k: v * mult for k, v in pri["cvr"].items()}


relative_fit = market.relative_fit  # re-exported for serving


# ------------------------------------------------------------------ 2. market per host


def host_markets(scores: list[PublisherScore], brief: str, eco: Economics, inputs: AdvertiserInputs,
                 days: int, category: str | None) -> list[dict]:
    """Per recommended host: click/conversion rates, page views in the flight, the bid landscape, and how
    much of it we may buy (aggressiveness + price caps)."""
    pri = _cfg()["priors"]
    pubs = catalog.publisher_by_id()
    picks = [s for s in scores if s.recommended]
    if not picks:
        return []
    top = picks[0].score
    picks = picks + [s for s in scores if s.exploration]  # exploration hosts get a small separate test budget (D26)
    cvr_all = category_cvr(category)
    k_share = _cfg()["aggressiveness"][inputs.aggressiveness]
    out = []
    for s in picks:
        fit = market.relative_fit(s.score, top)
        ctr, cvr = pri["ctr"]["mid"] * fit, {k: v * fit for k, v in cvr_all.items()}
        needed = market.landscape(s.publisher_id, ctr, exclude_brief=brief)
        # price caps: the advertiser's max CPC, and with a target CPA the click price that hits it on average
        caps = [c for c in (inputs.max_cpc_usd, eco.target_cpa * cvr["mid"] if eco.target_cpa else None) if c]
        price_cap = min(caps) if caps else math.inf
        k_max = sum(1 for i, n in enumerate(needed) if n <= price_cap and i < int(k_share * len(needed)))
        audience = s.score / top if inputs.objective != "conversions" else 1.0  # in-audience units (CVR covers conversions)
        unit_per_view = {"conversions": ctr * cvr["mid"], "clicks": ctr, "impressions": 1.0,
                         "reach": 1.0 / pri["avg_frequency"]}[inputs.objective] * audience
        views = pubs[s.publisher_id].monthly_impressions * days / 30
        out.append(dict(score=s, fit=fit, ctr=ctr, cvr=cvr, needed=needed, k_max=k_max, audience=audience,
                        unit_per_view=unit_per_view, views=views, bucket=views / len(needed),
                        competitor=any(f.startswith("competitor_conflict") for f in s.flags), explore=s.exploration))
    return out


def _bucket_cost(m: dict, k: int) -> float:
    """Expected spend for the k-th cheapest bucket of page views: we pay per click, the second price."""
    return m["bucket"] * m["ctr"] * m["needed"][k]


def allocate(markets: list[dict], budget: float, goal: float | None, share_cap: float) -> dict[str, int]:
    """Greedy: always buy the bucket with the most units per dollar. Each host's unit cost only rises as we
    buy more of it, so this is optimal for the separable problem (tested against brute force)."""
    taken = {m["score"].publisher_id: 0 for m in markets}
    spent = {pid: 0.0 for pid in taken}
    heap = [(-(m["unit_per_view"] * m["bucket"]) / _bucket_cost(m, 0), i) for i, m in enumerate(markets) if m["k_max"] > 0]
    heapq.heapify(heap)
    total, units = 0.0, 0.0
    while heap:
        _, i = heapq.heappop(heap)
        m = markets[i]
        pid, k = m["score"].publisher_id, taken[m["score"].publisher_id]
        cost = _bucket_cost(m, k)
        if total + cost > budget + 1e-9 or spent[pid] + cost > share_cap * budget + 1e-9:
            continue  # this host is done (budget or its share cap): its next buckets only cost more
        taken[pid] += 1
        spent[pid] += cost
        total += cost
        units += m["unit_per_view"] * m["bucket"]
        if goal is not None and units >= goal:
            break
        if taken[pid] < m["k_max"]:
            heapq.heappush(heap, (-(m["unit_per_view"] * m["bucket"]) / _bucket_cost(m, taken[pid]), i))
    return taken


def _summary(markets: list[dict], taken: dict[str, int]) -> tuple[float, float]:
    spend = sum(sum(_bucket_cost(m, k) for k in range(taken[m["score"].publisher_id])) for m in markets)
    units = sum(m["unit_per_view"] * m["bucket"] * taken[m["score"].publisher_id] for m in markets)
    return spend, units


def pricing_plan(eco: Economics, inputs: AdvertiserInputs, markets: list[dict]) -> PricingPlan:
    pri, pm = _cfg()["priors"], _cfg()["pricing_models"]
    markets = [m for m in markets if not m.get("explore")]  # the price range describes the main plan, not test hosts
    q = lambda p: round(sorted(market.quantile(m["needed"], p) for m in markets)[len(markets) // 2], 2)
    cpc = Range(low=q(0.25), mid=q(0.5), high=q(0.75)) if markets else None
    unlock = (f"tCPA unlocks after conversion tracking is verified and {pm['tcpa_requirements']['min_conversions']} "
              "click-through conversions are recorded.")
    agg = _cfg()["aggressiveness"][inputs.aggressiveness]
    why = (f"Market price to win a page view on these hosts: ${cpc.low:.2f}-${cpc.high:.2f} per click (median ${cpc.mid:.2f}), "
           f"from the simulated auction against the other campaigns. '{inputs.aggressiveness}' bidding tries to win up to "
           f"{agg:.0%} of each host's page views; the planned bid per host is the price that wins its planned share."
           if cpc else "No recommended hosts.")
    if eco.target_cpa:
        why += f" Target CPA ${eco.target_cpa:.2f}: bids stay at or below target x conversion rate on each host."
    if inputs.max_cpc_usd:
        why += f" Your max CPC ${inputs.max_cpc_usd:.2f} is never exceeded."
    if inputs.objective in ("impressions", "reach") and cpc:
        cpm = round(cpc.mid * pri["ctr"]["mid"] * 1000, 2)
        return PricingPlan(model="CPM", bidding="maximize", aggressiveness=inputs.aggressiveness, cpc_range=cpc,
                           cpm_bid=cpm, max_cpc=inputs.max_cpc_usd, target_cpa=None, tcpa_unlock=unlock,
                           rationale=f"{inputs.objective.capitalize()}: pay per 1,000 views (~${cpm} CPM at the median price). " + why)
    return PricingPlan(model="CPC", bidding=inputs.bidding, aggressiveness=inputs.aggressiveness, cpc_range=cpc,
                       max_cpc=inputs.max_cpc_usd, target_cpa=eco.target_cpa, tcpa_unlock=unlock,
                       rationale="Pay per click (verifiable without trusting a pixel). " + why)


# ------------------------------------------------------------------ 3-4. allocation + forecast


def budget_plan(markets: list[dict], inputs: AdvertiserInputs, today: date, gifting_moment: str | None) -> BudgetPlan | None:
    if not inputs.monthly_budget_usd or not markets:
        return None  # the advertiser chooses the budget; without it there is nothing to allocate
    cfg = _cfg()
    budget = inputs.monthly_budget_usd
    days = inputs.flight_days or cfg["budget"]["flight_days"]
    unit = OBJECTIVE_UNIT[inputs.objective]
    goal = float(inputs.goal_target) if inputs.goal_target else None
    stop_at_goal = goal is not None and inputs.when_goal_met == "stop"
    all_markets = markets
    markets = [m for m in all_markets if not m.get("explore")]      # the main plan: all the logic below works on these
    explore = [m for m in all_markets if m.get("explore")]
    budget_total = budget
    ex_budget = budget_total * catalog.config("scoring")["exploration"]["budget_share"] if explore and not stop_at_goal else 0.0
    budget = budget_total - ex_budget
    share_cap = inputs.max_share_per_host or cfg["allocation"]["max_share_per_host_default"]
    share_cap = max(share_cap, 1 / len(markets))  # a cap below 1/n hosts could never spend the budget

    taken = allocate(markets, budget, goal if stop_at_goal else None, share_cap)
    taken.update(allocate(explore, ex_budget, None, 1.0) if ex_budget else {m["score"].publisher_id: 0 for m in explore})
    spend, units = _summary(markets, taken)
    spend_ex, _ = _summary(explore, taken)
    _, units_uncapped = _summary(markets, allocate(markets, budget, goal if stop_at_goal else None, 1.0))
    full = {m["score"].publisher_id: m["k_max"] for m in markets}
    spend_full, max_deliverable = _summary(markets, full)
    lo, hi = cfg["allocation"]["forecast_spread"][inputs.objective]

    status, options, to_goal = "no_goal", [], None
    exhausted = all(taken[pid] >= full[pid] for pid in full)
    binding = "inventory" if exhausted else "budget"
    aggressive_more = inputs.aggressiveness != "aggressive"
    if goal is not None:
        if units >= goal * 0.999:
            status, binding = "reachable", ("goal" if stop_at_goal else binding)
            to_goal = round(_summary(markets, allocate(markets, math.inf, goal, 1.0))[0], 2)
            if stop_at_goal and spend < budget - 1:
                options.append(f"Goal reached with ~${spend:,.0f} of your ${budget:,.0f}; the rest stays unspent "
                               "(choose 'keep spending' to buy more).")
        elif max_deliverable >= goal:
            status, binding = "money_limited", "budget"
            to_goal = round(_summary(markets, allocate(markets, math.inf, goal, 1.0))[0], 2)
            options += [f"Raise the budget to ~${to_goal:,.0f} to reach {goal:,.0f} {unit}.",
                        f"Or accept ~{units:,.0f} {unit} at ${budget:,.0f}."]
        else:
            status, binding = "inventory_limited", "inventory"
            need_days = math.ceil(days * goal / max(max_deliverable, 1e-9))
            options += [f"At '{inputs.aggressiveness}' bidding these hosts offer ~{max_deliverable:,.0f} {unit} in {days} days, "
                        "whatever the budget."]
            if aggressive_more:
                options.append("Bid more aggressively to win a larger share of each host's page views (costs more per unit).")
            options += [f"Or extend the flight to ~{need_days} days, add hosts, or accept ~{max_deliverable:,.0f} {unit}."]
    elif not exhausted and spend < budget - 1 and not stop_at_goal and share_cap < 1:
        options.append(f"Your {share_cap:.0%} per-host cap leaves ~${budget - spend:,.0f} unspent: the other hosts can't "
                       f"absorb it at '{inputs.aggressiveness}' bidding. Raise or remove the cap, or add hosts.")
    elif exhausted and spend < budget - 1:
        options.append(f"Only ~${spend:,.0f} can be spent at '{inputs.aggressiveness}' bidding on these hosts"
                       + ("; bid more aggressively or add hosts." if aggressive_more else "; add hosts or lower the budget."))

    pri = cfg["priors"]
    allocations = []
    total_spend = spend + spend_ex
    for m in all_markets:
        s, pid = m["score"], m["score"].publisher_id
        k = taken[pid]
        spent_h = sum(_bucket_cost(m, i) for i in range(k))
        impressions = m["bucket"] * k
        clicks = impressions * m["ctr"]
        units_h = m["unit_per_view"] * impressions
        bid = round(m["needed"][k - 1], 2) if k else round(market.quantile(m["needed"], 0.25), 2)
        avg_paid = spent_h / clicks if clicks else 0.0
        p25, p50, p75 = (round(market.quantile(m["needed"], q), 2) for q in (0.25, 0.5, 0.75))
        allocations.append(Allocation(
            publisher_id=pid, publisher_name=s.publisher_name, share=round(spent_h / total_spend, 3) if total_spend else 0,
            budget_usd=round(spent_h, 2), cpc_bid_usd=bid, start_bid_usd=bid, win_share=round(k / len(m["needed"]), 3),
            avg_price_paid=round(avg_paid, 2), market_price=Range(low=p25, mid=p50, high=p75),
            audience_weight=round(m["audience"], 2),
            expected_clicks=Range(low=round(clicks * 0.8), mid=round(clicks), high=round(clicks * 1.2)),
            expected_conversions=Range(**{q: round(clicks * m["cvr"][q], 1) for q in ("low", "mid", "high")}),
            expected_units=Range(low=round(units_h * lo, 1), mid=round(units_h, 1), high=round(units_h * hi, 1)),
            capacity_usd=round(sum(_bucket_cost(m, i) for i in range(m["k_max"])), 2),
            expected_impressions=round(impressions), expected_reach=round(impressions / pri["avg_frequency"]),
            requires_host_approval=m["competitor"], exploration=bool(m.get("explore")),
            rationale=(("EXPLORATION TEST (not in the main plan: " + s.reason.split(" Flags:")[0].replace("Excluded: ", "") + "). "
                        if m.get("explore") else "") + (f"not funded at this budget: cheaper {unit} are available on the other hosts first "
                        f"(market price here ${p25:.2f}-${p75:.2f}); it gets budget as the budget grows or if a funded host declines"
                        if not k else
                        f"market price ${p25:.2f}-${p75:.2f} (median ${p50:.2f}); bidding ${bid:.2f} wins ~{k / len(m['needed']):.0%} "
                        f"of page views, paying ~${avg_paid:.2f} per click (second price)")
                       + (f"; {m['audience']:.0%} of units counted as in-audience" if m["audience"] < 1 else "")
                       + ("; host also sells this kind of product: confirm it allows competing brands" if m["competitor"] else "")),
        ))
    pacing = "even daily pacing"
    if gifting_moment:
        pacing = f"front-loaded x{cfg['budget']['gifting_front_load']} during '{gifting_moment}', even otherwise"
    if stop_at_goal:
        pacing += "; stop when the goal is reached"
    total_impr = sum(a.expected_impressions for a in allocations)
    return BudgetPlan(
        monthly_budget_usd=round(budget_total, 2), planned_spend_usd=round(total_spend, 2), objective=inputs.objective, unit=unit,
        goal_target=inputs.goal_target, when_goal_met=inputs.when_goal_met, aggressiveness=inputs.aggressiveness,
        max_share_per_host=round(share_cap, 2),
        forecast=Range(low=round(units * lo, 1), mid=round(units, 1), high=round(units * hi, 1)),
        forecast_without_share_cap=round(units_uncapped, 1), goal_status=status, binding_constraint=binding,
        spend_to_reach_goal_usd=to_goal, max_deliverable=round(max_deliverable, 1),
        spend_at_full_inventory_usd=round(spend_full, 2), replan_options=options,
        expected_impressions=total_impr, expected_reach=round(total_impr / pri["avg_frequency"]),
        daily_cap_usd=round(budget_total / days, 2), flight_start=today.isoformat(),
        flight_end=(today + timedelta(days=days)).isoformat(), pacing=pacing, allocations=allocations, warnings=[],
    )


# ------------------------------------------------------------------ 5. bid strategy


def bid_strategy(pricing: PricingPlan, markets: list[dict], inputs: AdvertiserInputs, plan: BudgetPlan | None) -> BidStrategy:
    cfg = _cfg()["bid_strategy"]
    floor = _cfg()["economics"]["cpc_floor"]
    adj = cfg["adjustments"]
    planned = {a.publisher_id: a.cpc_bid_usd for a in plan.allocations} if plan else {}
    host_bids = []
    for m in markets:
        pid = m["score"].publisher_id
        ceiling = m["needed"][m["k_max"] - 1] if m["k_max"] else market.quantile(m["needed"], 0.25)
        if inputs.max_cpc_usd:
            ceiling = min(ceiling, inputs.max_cpc_usd)  # the advertiser's cap always wins
        start = planned.get(pid) or round(market.quantile(m["needed"], 0.5), 2)
        host_bids.append(HostBid(publisher_id=pid, start_bid=round(min(start, ceiling), 2), max_bid=round(ceiling, 2), floor=floor))
    pacing = BidAdjustment(signal="pacing", min_multiplier=adj["pacing"]["min"], max_multiplier=adj["pacing"]["max"],
                           reason="Under-delivering vs the daily plan -> bid up a little; over-delivering -> bid down.")
    conv = BidAdjustment(signal="conversion_likelihood", min_multiplier=adj["conversion_likelihood"]["min"],
                         max_multiplier=adj["conversion_likelihood"]["max"],
                         reason="Bid higher when the shopper looks like a likely converter (persona match vs the host's average shopper).")
    learning = {"name": "learning", "until": f"{cfg['learning_phase']['days']} days",
                "rule": "planned bid per host; pacing only", "why": "stable bids so measured rates aren't confounded"}
    ceiling_note = (f"the ceiling per host is the price that wins your '{inputs.aggressiveness}' share"
                    + (f", never above your ${inputs.max_cpc_usd:.2f} max CPC" if inputs.max_cpc_usd else ""))
    if pricing.model == "CPM":
        return BidStrategy(current_phase="fixed_cpm", host_bids=host_bids, adjustments=[pacing],
                           phases=[{"name": "fixed_cpm", "rule": f"${pricing.cpm_bid} CPM; pacing only"}],
                           rationale=f"Buying views at market prices; {ceiling_note}.")
    if inputs.bidding == "maximize":
        rule = "planned bid x pacing" + (" x conversion likelihood" if inputs.objective == "conversions" else "") + ", up to the ceiling"
        return BidStrategy(current_phase="learning", host_bids=host_bids,
                           adjustments=[pacing] + ([conv] if inputs.objective == "conversions" else []),
                           phases=[learning, {"name": "maximize", "rule": rule, "why": "most units for the budget"}],
                           rationale=f"Maximise {OBJECTIVE_UNIT[inputs.objective]} within the budget; {ceiling_note}.")
    return BidStrategy(
        current_phase="learning", host_bids=host_bids, adjustments=[pacing, conv],
        phases=[learning,
                {"name": "enhanced_cpc", "until": pricing.tcpa_unlock, "rule": "planned bid x conversion likelihood x pacing, up to the ceiling",
                 "why": "aim at the target on average while learning who converts"},
                {"name": "target_cpa", "rule": f"Disco bids automatically toward ${pricing.target_cpa:.2f} average CPA",
                 "why": "once Disco can predict this brand's conversion rate"}],
        rationale=f"Target ${pricing.target_cpa:.2f} CPA as an average, not a per-click limit; {ceiling_note}.",
    )
