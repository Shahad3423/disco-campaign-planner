# Eval report

Planning date 2026-10-30. Golden set: blind labels (eval/README.md).

| metric | value | baseline | delta |
|---|---|---|---|
| n | 15 | 15 | +0.000 |
| gate_accuracy | 0.867 | 0.867 | +0.000 |
| gate_accuracy_incl_adjacent | 1.0 | 1.0 | +0.000 |
| must_include_recall@5 | 0.7 | 0.7 | +0.000 |
| must_exclude_violations | 0 | 0 | +0.000 |
| precision | 0.939 | 0.939 | +0.000 |
| competitor_flag_recall | 0.625 | 0.625 | +0.000 |
| implausible_personas_selected | 5 | 5 | +0.000 |
| plausible_persona_recall | 0.833 | 0.833 | +0.000 |
| budget_invariants_ok | 1.0 | 1.0 | +0.000 |
| creative_rule_pass_rate | 1.0 | 1.0 | +0.000 |
| creative_review_pass_rate | 0.618 | 0.767 | -0.149 |
| creative_mean_persona_fit | 4.296 | 4.353 | -0.057 |
| creative_unsupported_claims | 26 | 40 | -14.000 |
| creative_max_body_jaccard | 0.623 | 0.695 | -0.072 |
| wall_time_s | 0.6 | 0.5 | +0.100 |

| # | gate (exp) | recall@5 | excl. viol. | prec. | comp. flag | bad personas | recommended |
|---|---|---|---|---|---|---|---|
| 1 | proceed | 1.0 | - | 1.0 | 1.0 | - | pub_007, pub_009, pub_018 |
| 2 | proceed | 1.0 | - | 0.67 | 1.0 | - | pub_002, pub_017, pub_016, pub_003, pub_006, pub_010 |
| 3 | clarify (proceed) | 0.0 | - | 1.0 | 1.0 | persona_001 | pub_020, pub_012 |
| 4 | proceed | 0.6666666666666666 | - | 1.0 | None | - | pub_014, pub_011 |
| 5 | clarify | None | - | None | None | - |  |
| 6 | proceed | 1.0 | - | 0.67 | None | - | pub_002, pub_017, pub_016 |
| 7 | off_network | None | - | None | None | - |  |
| 8 | clarify | None | - | None | None | - |  |
| 9 | proceed | 0.5 | - | 1.0 | 0.0 | persona_010 | pub_001, pub_014 |
| 10 | proceed | 1.0 | - | 1.0 | None | persona_010 | pub_005, pub_004, pub_016 |
| 11 | clarify (proceed) | 0.3333333333333333 | - | 1.0 | 0.0 | persona_001 | pub_012, pub_003 |
| 12 | proceed | None | - | 1.0 | 0.0 | - | pub_009, pub_007, pub_018 |
| 13 | proceed | 1.0 | - | 1.0 | 1.0 | persona_001 | pub_012, pub_002, pub_003, pub_017 |
| 14 | proceed | 0.5 | - | 1.0 | 1.0 | - | pub_011, pub_014 |
| 15 | reject_low_signal | None | - | None | None | - |  |
