# Scoring sensitivity: Morris screening

40 constants, 20 trajectories (820 re-rankings of 15 briefs), each constant over [x0.5, x1.5] of its value (exponents/p over fixed ranges).
Current config: precision 0.970, must-include recall@5 0.600.

mu* = how much the top-5 moves when this constant moves across its range (0 = never; 1 = completely);
sigma = how much that depends on the other constants. Signed columns: average effect on precision/recall.

| constant | mu* (top-5 change) | sigma | effect on precision | effect on recall |
|---|---|---|---|---|
| price_exponent | 0.174 | 0.229 | -0.009 | -0.221 |
| quality_p | 0.134 | 0.091 | +0.006 | +0.131 |
| shape.neutral | 0.118 | 0.217 | +0.026 | +0.065 |
| relevance_exponent | 0.081 | 0.111 | +0.076 | -0.193 |
| recommend.relative_to_top | 0.075 | 0.097 | +0.045 | -0.105 |
| recommend.min_score | 0.073 | 0.138 | +0.019 | -0.049 |
| category.category_only_cap | 0.070 | 0.087 | -0.052 | +0.031 |
| recommend.min_category_fit | 0.065 | 0.097 | +0.033 | -0.019 |
| shape.income_gap_penalty | 0.047 | 0.065 | +0.037 | +0.068 |
| category.attribute_only_cap | 0.039 | 0.063 | -0.023 | +0.018 |
| shape.tier_match_value | 0.035 | 0.050 | -0.031 | -0.004 |
| shape.price_spread_above | 0.034 | 0.046 | -0.004 | +0.045 |
| quality_weights.persona_fit | 0.031 | 0.061 | +0.008 | +0.001 |
| category.attribute_bonus | 0.024 | 0.034 | -0.012 | +0.011 |
| shape.notes_values.neutral | 0.021 | 0.035 | -0.022 | +0.008 |
| shape.notes_values.aligned | 0.020 | 0.034 | +0.005 | +0.015 |
| recommend.strong_min_price_fit | 0.014 | 0.027 | +0.012 | -0.007 |
| shape.seasonality_values.non_gift_product | 0.014 | 0.026 | +0.001 | +0.012 |
| quality_weights.scale | 0.011 | 0.022 | -0.003 | -0.015 |
| quality_weights.audience_fit | 0.010 | 0.023 | -0.002 | +0.000 |
| quality_weights.notes_fit | 0.009 | 0.021 | -0.001 | +0.000 |
| persona_shape.affinity_step | 0.007 | 0.017 | +0.009 | +0.015 |
| persona_shape.affinity_base | 0.005 | 0.018 | -0.002 | -0.001 |
| quality_weights.seasonality | 0.004 | 0.015 | +0.005 | +0.004 |
| persona_shape.host_category_miss | 0.004 | 0.011 | +0.000 | +0.000 |
| persona_shape.host_category_match | 0.003 | 0.015 | +0.000 | -0.003 |
| category.extra_specific_bonus | 0.003 | 0.015 | +0.000 | +0.000 |
| shape.seasonality_values.in_season | 0.003 | 0.011 | -0.002 | +0.002 |
| persona_shape.gender_balanced | 0.001 | 0.003 | +0.004 | +0.000 |
| penalties.notes_misaligned | 0.000 | 0.000 | +0.000 | +0.000 |
| generic_only_cap | 0.000 | 0.000 | +0.000 | +0.000 |
| personas.min_score | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.price_spread_below | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.notes_values.misaligned | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.seasonality_values.off_season | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.seasonality_values.non_gift_host | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.overlap_neutral | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.gender_mismatch | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.audience_gender_weight | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.audience_age_floor | 0.000 | 0.000 | +0.000 | +0.000 |
