# Scoring sensitivity (one-at-a-time, x0.5 / x1.5)

Baseline: precision 0.924, must-include recall@5 0.700. Lower top-5 Jaccard = rankings move more.

| constant | worst top-5 Jaccard | at factor | d precision | d recall |
|---|---|---|---|---|
| recommend.relative_to_top | 0.748 | x1.5 | +0.076 | -0.083 |
| priors.competitor_approval_probability | 0.795 | x0.5 | -0.015 | +0.0 |
| category.category_only_cap | 0.818 | x0.5 | +0.056 | -0.1 |
| category.attribute_only_cap | 0.917 | x1.5 | -0.049 | +0.0 |
| recommend.min_category_fit | 0.917 | x0.5 | -0.035 | +0.0 |
| quality_weights.persona_fit | 0.924 | x0.5 | +0.0 | +0.0 |
| recommend.strong_min_price_fit | 0.964 | x0.5 | -0.024 | +0.0 |
| quality_weights.audience_fit | 0.97 | x0.5 | +0.0 | +0.0 |
| quality_weights.notes_fit | 0.97 | x0.5 | +0.0 | +0.0 |
| quality_weights.seasonality | 0.97 | x1.5 | +0.0 | +0.0 |
| price_exponent | 0.97 | x0.5 | +0.0 | +0.0 |
| NEUTRAL | 0.97 | x0.5 | +0.0 | +0.0 |
| quality_weights.scale | 1.0 | x0.5 | +0.0 | +0.0 |
| category.extra_specific_bonus | 1.0 | x0.5 | +0.0 | +0.0 |
| category.attribute_bonus | 1.0 | x0.5 | +0.0 | +0.0 |
| penalties.notes_misaligned | 1.0 | x0.5 | +0.0 | +0.0 |
| recommend.min_score | 1.0 | x0.5 | +0.0 | +0.0 |
| personas.min_score | 1.0 | x0.5 | +0.0 | +0.0 |
