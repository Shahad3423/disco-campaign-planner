# Eval report

Planning date 2026-10-30. Golden set: blind labels (eval/README.md).

| metric | value | baseline | delta |
|---|---|---|---|
| n | 15 | 15 | +0.000 |
| gate_accuracy | 0.867 | 0.867 | +0.000 |
| gate_accuracy_incl_adjacent | 1.0 | 1.0 | +0.000 |
| must_include_recall@5 | 0.767 | 0.8 | -0.033 |
| must_exclude_violations | 2 | 2 | +0.000 |
| precision | 0.917 | 0.848 | +0.069 |
| competitor_flag_recall | 0.875 | 0.875 | +0.000 |
| implausible_personas_selected | 5 | 5 | +0.000 |
| plausible_persona_recall | 0.819 | 0.819 | +0.000 |
| budget_invariants_ok | 1.0 | 1.0 | +0.000 |
| wall_time_s | 0.1 | 107.8 | -107.700 |

| # | gate (exp) | recall@5 | excl. viol. | prec. | comp. flag | bad personas | recommended |
|---|---|---|---|---|---|---|---|
| 1 | proceed | 1.0 | - | 1.0 | 1.0 | - | pub_007, pub_018, pub_009 |
| 2 | clarify (proceed) | 1.0 | pub_005 | 0.67 | 1.0 | - | pub_016, pub_003, pub_002, pub_004, pub_005, pub_017 |
| 3 | proceed | 1.0 | - | 1.0 | 1.0 | persona_001 | pub_020, pub_012, pub_001, pub_008 |
| 4 | proceed | 0.6666666666666666 | - | 1.0 | None | - | pub_014, pub_011 |
| 5 | clarify | None | - | 1.0 | None | - | pub_012 |
| 6 | proceed | 1.0 | pub_005 | 0.33 | None | - | pub_016, pub_005, pub_004, pub_002, pub_017, pub_010 |
| 7 | off_network | None | - | None | None | - |  |
| 8 | clarify | None | - | None | None | - |  |
| 9 | proceed | 0.5 | - | 1.0 | 1.0 | persona_010 | pub_014, pub_011, pub_001 |
| 10 | proceed | 1.0 | - | 1.0 | None | persona_003 | pub_005, pub_004, pub_016 |
| 11 | clarify (proceed) | 0.0 | - | 1.0 | 0.0 | persona_001 | pub_012 |
| 12 | proceed | None | - | 1.0 | 1.0 | - | pub_009, pub_007, pub_018 |
| 13 | proceed | 1.0 | - | 1.0 | 1.0 | persona_001 | pub_002, pub_003, pub_012, pub_017 |
| 14 | proceed | 0.5 | - | 1.0 | 1.0 | - | pub_011, pub_014 |
| 15 | reject_low_signal | None | - | None | None | - |  |
