# Golden labels for the 15 example advertisers

`golden.yaml` holds hand-written expected outcomes for each advertiser in `data/example_advertisers.txt`.

## Blind labeling

The labels were written from the brief, `docs/DECISIONS.md`, and the three data files only. The author did not read, run, or wait for any ranking, gating, or scoring code, so the labels are independent judgment and can fairly grade the system. Do not tune labels to match system output; if a label is wrong, change it in a separate PR with a reason.

## Fields

- `expected_gate`: `proceed`, `clarify` (vague but some signal), `reject_low_signal` (no signal), or `off_network` (not a consumer DTC advertiser; D7).
- `must_include`: publishers any sensible system should put in its top 5. Strict: only clear calls, and often empty. Competitors are never listed here.
- `nice_to_have`: plausible extra picks; no penalty if absent.
- `must_exclude`: clearly wrong recommendations (wrong category, wrong audience, or the gate says zero publishers).
- `competitor_conflict`: publishers selling a directly competing product (D1). These should be flagged and down-ranked, not necessarily excluded, so they appear in neither `must_include` nor `must_exclude`. Some are soft (a grocer that probably stocks the product) and say so in the rationale.
- `plausible_personas` / `implausible_personas`: grounded in persona `category_affinities`, `disinterested_in`, `price_sensitivity`, and `typical_aov_usd`. A few weakly grounded calls are noted in the rationale.
- `claims_to_watch`: claims a creative must not invent or overstate.
- `rationale`: the catalog fields behind the labels.
- `difficulty`: `easy`, `medium`, or `hard`.

## Assumptions

- Today is 2026-10-30, so gifting season is imminent and Heartfoot's Nov-Dec uplift applies. A 6-week lead time (advertiser 10) lands about Dec 11.
- Publisher category data is all we have, so competitor flags are inferred from category and subcategories, not from real inventory.
- Labels are for the mock catalog only; the maximum catalog AOV is $198.
