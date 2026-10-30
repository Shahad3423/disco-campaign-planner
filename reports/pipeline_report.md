# Whole-pipeline evaluation: before vs after the scoring changes

Changes: competitor = flag + host setting (no score/budget penalty, majority flags, fallback host); constants in config; power-mean aggregation (identical at p=1); exponents kept hand-set (a dev-only fit didn't beat them on test). Golden 'before' = eval_v3c (matching only; creative metrics in eval_v3_full).

## Golden set (15 examples, blind labels)

| metric | before | after | delta |
|---|---|---|---|
| gate_accuracy | 0.867 | 0.867 | +0.000 |
| must_include_recall@5 | 0.7 | 0.6 | -0.100 |
| must_exclude_violations | 1 | 0 | -1.000 |
| precision | 0.924 | 0.97 | +0.046 |
| competitor_flag_recall | 0.875 | 0.625 | -0.250 |
| implausible_personas_selected | 5 | 5 | +0.000 |
| plausible_persona_recall | 0.833 | 0.833 | +0.000 |
| budget_invariants_ok | 1.0 | 1.0 | +0.000 |
| creative_rule_pass_rate | - | 1.0 |  |
| creative_review_pass_rate | - | 0.781 |  |
| creative_mean_persona_fit | - | 4.029 |  |
| creative_unsupported_claims | - | 6 |  |
| paraphrase stability (top-5 Jaccard) | - | 0.917 |  |
| prompt-injection pass rate | - | 1.0 |  |

## Holdout TEST brands (51 of 100, never used to fit or tune)

| metric | before | after | delta |
|---|---|---|---|
| gate agreement with writer's expectation | 0.627 | 0.627 | +0.000 |
| errors | 0 | 0 | +0.000 |
| edge: mean hosts recommended | 1.12 | 1.12 | +0.000 |
| edge: weak-fit rate when planned | 0 | 0 | +0.000 |
| in_catalog: mean hosts recommended | 3.61 | 3.14 | -0.470 |
| in_catalog: weak-fit rate when planned | 0.143 | 0.071 | -0.072 |
| out_of_catalog: mean hosts recommended | 1.67 | 1.73 | +0.060 |
| out_of_catalog: weak-fit rate when planned | 0.4 | 0.4 | +0.000 |
| hosts never recommended (count) | 0 | 0 | +0.000 |
| most-recommended host's share of plans | 0.31 | 0.31 | +0.000 |

## Competitor flags (holdout TEST plans)

| metric | before | after | delta |
|---|---|---|---|
| flagged_share_of_recommended | 0.222 | 0.276 | +0.054 |
| advertisers_with_a_flag | 0.417 | 0.5 | +0.083 |
| top_pick_is_flagged | 0.167 | 0.479 | +0.312 |
| flagged_share_of_all_pairs | 0.037 | 0.035 | -0.002 |
| mean_hosts_recommended | 2.81 | 2.56 | -0.250 |

## Auctions (one slot per page; 15 demo campaigns vs demo + holdout rivals)

| metric | after |
|---|---|
| host_slots | 22 |
| mean_win_rate | 0.273 |
| share_at_0 | 0.682 |
| share_at_100 | 0.227 |
| share_mixed | 0.091 |
