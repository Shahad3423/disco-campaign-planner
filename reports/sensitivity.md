# Scoring sensitivity: Morris screening

37 constants, 20 trajectories (760 re-rankings of 15 briefs), each constant over [x0.5, x1.5] of its value (exponents/p over fixed ranges).
Current config: precision 0.939, must-include recall@5 0.700.

mu* = how much the top-5 moves when this constant moves across its range (0 = never; 1 = completely);
sigma = how much that depends on the other constants. Signed columns: average effect on precision/recall.

| constant | mu* (top-5 change) | sigma | effect on precision | effect on recall |
|---|---|---|---|---|
| quality_p | 0.145 | 0.102 | +0.002 | +0.136 |
| relevance_exponent | 0.129 | 0.162 | +0.063 | -0.150 |
| recommend.category_only_min_persona_fit | 0.122 | 0.104 | +0.042 | -0.120 |
| price_exponent | 0.100 | 0.078 | +0.027 | -0.150 |
| recommend.relative_to_top | 0.061 | 0.075 | +0.037 | -0.070 |
| category.category_only_cap | 0.051 | 0.071 | -0.026 | +0.029 |
| shape.income_gap_penalty | 0.048 | 0.044 | +0.005 | +0.083 |
| shape.notes_values.neutral | 0.042 | 0.061 | -0.020 | -0.019 |
| recommend.min_score | 0.041 | 0.064 | +0.010 | -0.062 |
| recommend.min_category_fit | 0.040 | 0.072 | +0.036 | -0.035 |
| shape.notes_values.aligned | 0.037 | 0.049 | +0.012 | +0.034 |
| quality_weights.persona_fit | 0.032 | 0.048 | -0.001 | +0.010 |
| category.attribute_only_cap | 0.026 | 0.066 | -0.012 | +0.015 |
| shape.price_spread_above | 0.024 | 0.037 | -0.008 | +0.034 |
| persona_shape.host_category_match | 0.024 | 0.036 | +0.000 | +0.016 |
| quality_weights.scale | 0.022 | 0.036 | +0.003 | -0.019 |
| persona_shape.host_category_miss | 0.017 | 0.029 | -0.011 | +0.000 |
| quality_weights.notes_fit | 0.012 | 0.020 | -0.000 | +0.017 |
| category.attribute_bonus | 0.009 | 0.017 | -0.004 | +0.000 |
| shape.seasonality_values.non_gift_host | 0.007 | 0.022 | +0.001 | +0.007 |
| quality_weights.audience_fit | 0.004 | 0.012 | -0.003 | -0.004 |
| quality_weights.seasonality | 0.002 | 0.010 | -0.000 | -0.004 |
| category.extra_specific_bonus | 0.002 | 0.010 | +0.000 | +0.004 |
| persona_shape.affinity_base | 0.002 | 0.010 | +0.000 | +0.000 |
| persona_shape.affinity_step | 0.002 | 0.009 | +0.002 | +0.004 |
| recommend.strong_min_price_fit | 0.001 | 0.005 | -0.002 | -0.008 |
| shape.seasonality_values.in_season | 0.001 | 0.005 | -0.002 | +0.000 |
| penalties.notes_misaligned | 0.000 | 0.000 | +0.000 | +0.000 |
| generic_only_cap | 0.000 | 0.000 | +0.000 | +0.000 |
| personas.min_score | 0.000 | 0.000 | +0.000 | +0.000 |
| personas.variants_per_persona | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.notes_values.misaligned | 0.000 | 0.000 | +0.000 | +0.000 |
| shape.seasonality_values.off_season | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.gender_balanced | 0.000 | 0.000 | +0.001 | +0.000 |
| persona_shape.gender_mismatch | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.audience_gender_weight | 0.000 | 0.000 | +0.000 | +0.000 |
| persona_shape.audience_age_floor | 0.000 | 0.000 | +0.000 | +0.000 |
