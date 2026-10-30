"""Auction insights: what an advertiser can actually act on, without seeing any rival's bid.

Bids are sealed (see DECISIONS D19). What advertisers get instead is aggregate feedback per host:
how often they win, what they pay, who they lose to (by name only when it's a pool, never a bid),
and what bid it would take to win more, compared with their break-even ceiling. That last line
turns "I'm losing" into a decision: raise the target CPA, or stop chasing this host.

Here the aggregate is simulated: sample shoppers x the host, with the other campaigns in the pool
as competitors. In production it's computed from the auction logs.
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timezone
from itertools import product

from pydantic import BaseModel

from disco_planner.data import catalog
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.decision import serve
from disco_planner.serving.features import ShopperSignals

SAMPLE_NAMES = ["Emily", "Sarah", "Jessica", "Michael", "David", "James", "Jordan", "Taylor"]
SAMPLE_ZIPS = ["10001", "94103", "80435", "33139", "60614", "98101", "85004", "02139"]
SAMPLE_TIME = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
WIN_TARGETS = (0.25, 0.5, 0.75)  # "what would it take to win this share of auctions?"


class HostInsight(BaseModel):
    publisher_id: str
    publisher_name: str
    pricing_model: str
    auctions: int                  # simulated page views where we were eligible
    competitors_on_host: int       # distinct other campaigns that ever bid here
    win_rate: float
    avg_price_paid: float | None   # per click (CPC) or per 1,000 (CPM), when we won
    avg_bid: float
    max_bid: float                 # break-even ceiling (never exceeded)
    top_rivals: list[tuple[str, float]]   # (campaign, share of the auctions we lost that it won)
    bid_for_win_rate: dict[str, float]    # win target -> bid needed (CPC), estimated
    verdict: str
    suggestion: str


def _bid_needed(ours: dict, rival_ecpm: float) -> float:
    """Bid at which our eCPM just beats the rival's (second price rule: rival / (ctr x 1000) + 1 cent)."""
    if ours["pricing_model"] == "CPM":
        return rival_ecpm + 0.01
    return rival_ecpm / (ours["expected_ctr"] * 1000) + 0.01 if ours["expected_ctr"] else float("inf")


def auction_insights(plan: CampaignPlan, competitors: list[CampaignPlan]) -> list[HostInsight]:
    me = plan.profile.product or plan.brief[:40]
    pool = [plan] + [c for c in competitors if (c.profile.product or c.brief[:40]) != me]
    out = []
    for alloc in plan.config.budget.allocations if plan.config.budget else []:
        pid = alloc.publisher_id
        wins, paid, bids, needed_to_win, rivals = 0, [], [], [], Counter()
        other_campaigns, auctions = set(), 0
        for name, zip_ in product(SAMPLE_NAMES, SAMPLE_ZIPS):
            d = serve(ShopperSignals(publisher_id=pid, zip=zip_, first_name=name, zip_source="billing"), SAMPLE_TIME, pool)
            ours = next((x for x in d.bid_landscape if x["campaign"] == me), None)
            if ours is None:
                continue  # we didn't bid on this page view (e.g. no eligible creative)
            auctions += 1
            bids.append(ours["bid"])
            other_campaigns.update(x["campaign"] for x in d.bid_landscape if x["campaign"] != me)
            winner = d.offers[0] if d.offers else None
            if winner and winner.campaign == me:
                wins += 1
                paid.append(winner.price_paid)
            else:
                top_rival = next((x for x in d.bid_landscape if x["campaign"] != me), None)
                rivals[winner.campaign if winner else "(reserve not met)"] += 1
                needed_to_win.append(_bid_needed(ours, top_rival["ecpm"]) if top_rival else ours["bid"])
        if not auctions:
            continue
        lost = auctions - wins
        # Bid needed for a win target: the lowest bid that beats the rival in at least that share
        # of ALL auctions. We already win `wins/auctions`; losses are sorted by how cheaply they flip.
        needed = {}
        ranked_losses = sorted(needed_to_win)
        for target in WIN_TARGETS:
            extra = round(target * auctions) - wins
            if extra <= 0:
                needed[f"{target:.0%}"] = round(statistics.mean(bids), 2)
            elif extra <= len(ranked_losses):
                needed[f"{target:.0%}"] = round(ranked_losses[extra - 1], 2)
        win_rate, max_bid = wins / auctions, alloc.cpc_bid_usd
        avg_bid = round(statistics.mean(bids), 2)
        affordable = [k for k, v in needed.items() if v <= max_bid]
        if win_rate >= 0.5:
            verdict, tip = "winning", (f"You win {win_rate:.0%} of page views here and pay ${statistics.mean(paid):.2f} on average "
                                       f"vs your ${avg_bid:.2f} bid: second price is working in your favour.")
        elif affordable and len(affordable) < len(needed):
            top = affordable[-1]
            verdict, tip = "winnable", (f"Winning {top} of page views needs a bid of about ${needed[top]:.2f}, still under your "
                                        f"${max_bid:.2f} break-even. Raise the start bid; going beyond that would cost more than a customer is worth.")
        elif affordable:
            verdict, tip = "winnable", f"You can reach every target below your ${max_bid:.2f} break-even; raise the start bid to ${needed[affordable[-1]]:.2f}."
        else:
            first = next(iter(needed.values()), None)
            verdict, tip = "not worth chasing", (
                (f"Even the cheapest extra wins need ~${first:.2f}, above your ${max_bid:.2f} break-even." if first else
                 "Rivals outbid you on nearly every page view.")
                + " Either raise your target CPA (accept paying more per customer) or move this budget to other hosts.")
        out.append(HostInsight(
            publisher_id=pid, publisher_name=alloc.publisher_name, pricing_model=plan.config.pricing.model,
            auctions=auctions, competitors_on_host=len(other_campaigns), win_rate=round(win_rate, 3),
            avg_price_paid=round(statistics.mean(paid), 2) if paid else None, avg_bid=avg_bid, max_bid=max_bid,
            top_rivals=[(k, round(v / lost, 2)) for k, v in rivals.most_common(3)] if lost else [],
            bid_for_win_rate=needed, verdict=verdict, suggestion=tip,
        ))
    return out
