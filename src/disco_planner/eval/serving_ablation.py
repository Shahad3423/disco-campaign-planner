"""Does each shopper signal earn its place? Re-run serving decisions with a signal removed
and count how often the decision (top offer + creative variant) changes.

    python -m disco_planner.eval.serving_ablation
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import product
from pathlib import Path

from disco_planner.data import catalog
from disco_planner.planning.pipeline import CampaignPlan
from disco_planner.serving.decision import serve
from disco_planner.serving.features import ShopperSignals

NAMES = ["Emily", "Sarah", "Jessica", "Linda", "Barbara", "Ava", "Michael", "David", "James", "Robert",
         "Ethan", "Jordan", "Taylor", "Casey", "Riley"]
ZIPS = ["10001", "94103", "80435", "33139", "60614", "98101", "85004", "59001", "02139", "75201"]
TIMES = [datetime(2026, 11, 20, 18, tzinfo=timezone.utc), datetime(2027, 1, 5, 4, tzinfo=timezone.utc)]


def _key(d) -> tuple:
    top = d.offers[0] if d.offers else None
    return (top.campaign, top.persona_id) if top else (None, None)


def main() -> None:
    plans = [CampaignPlan.model_validate_json(p.read_text()) for p in sorted((catalog.REPO_ROOT / "demo_cache" / "plans").glob("*.json"))]
    hosts = [p.id for p in catalog.publishers()]
    total = changed_name = changed_zip = changed_time = served = 0
    for host, name, zip_, when in product(hosts, NAMES, ZIPS, TIMES):
        full = serve(ShopperSignals(publisher_id=host, zip=zip_, first_name=name, zip_source="billing"), when, plans)
        if not full.offers:
            continue
        served += 1
        no_name = serve(ShopperSignals(publisher_id=host, zip=zip_, first_name=name, zip_source="billing"), when, plans, use_name=False)
        no_zip = serve(ShopperSignals(publisher_id=host, zip="", first_name=name), when, plans)
        other_time = serve(ShopperSignals(publisher_id=host, zip=zip_, first_name=name, zip_source="billing"),
                           TIMES[1] if when == TIMES[0] else TIMES[0], plans)
        changed_name += _key(full) != _key(no_name)
        changed_zip += _key(full) != _key(no_zip)
        changed_time += _key(full) != _key(other_time)
        total += 1
    lines = [
        "# Serving ablation: which shopper signals change decisions?", "",
        f"{len(hosts)} hosts x {len(NAMES)} names x {len(ZIPS)} zips x {len(TIMES)} timestamps; "
        f"{served} requests had at least one eligible campaign.", "",
        "| signal removed | decisions changed (top campaign or variant) |", "|---|---|",
        f"| first name (gender/age prior) | {changed_name / total:.1%} |",
        f"| zip (geo + local time) | {changed_zip / total:.1%} |",
        f"| timestamp (Nov 20 vs Jan 5) | {changed_time / total:.1%} |", "",
        "The platform (host) is fixed in every row: it is the dominant signal by construction, since",
        "it decides which campaigns are eligible at all. A weak signal that changes many decisions is a",
        "warning sign (flip-flopping between near-tied personas), not a sign of value: see the",
        "switch_margin setting in config/serving.yaml and docs/EXPERIMENTS.md.",
    ]
    # Split the name effect: does it change WHICH CAMPAIGN, or only WHICH VARIANT? And does
    # low-confidence (unisex) naming move decisions less, as the tempering intends?
    lines += ["", "| name group | variant or campaign changed | campaign changed |", "|---|---|---|"]
    for group, names in {"female-coded": NAMES[:6], "male-coded": NAMES[6:11], "unisex": NAMES[11:]}.items():
        n = any_change = campaign_change = 0
        for host, name, zip_ in product(hosts, names, ZIPS):
            sig = ShopperSignals(publisher_id=host, zip=zip_, first_name=name, zip_source="billing")
            with_name, without = serve(sig, TIMES[0], plans), serve(sig, TIMES[0], plans, use_name=False)
            if not with_name.offers:
                continue
            n += 1
            any_change += _key(with_name) != _key(without)
            campaign_change += _key(with_name)[0] != _key(without)[0]
        lines.append(f"| {group} | {any_change / n:.0%} | {campaign_change / n:.0%} |")
    out = catalog.REPO_ROOT / "reports" / "serving_ablation.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
