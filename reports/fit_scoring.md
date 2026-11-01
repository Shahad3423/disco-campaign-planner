# Fitting the scoring constants from pairwise labels

Labels: order-consistent, decisive LLM-judge pairs (prompts/pairwise_judge.md, both orders asked). Fit on 223 DEV pairs; 252 TEST pairs never used to fit.

## TEST brands (never used to fit): pairwise agreement with the judge

| model | accuracy | log loss |
|---|---|---|
| hand-set config | 0.790 | 0.535 |
| fitted on dev | 0.786 | 0.516 |

## DEV brands: 5-fold CV by advertiser

| model | accuracy | log loss |
|---|---|---|
| hand-set config (scale only fitted) | 0.745 | 0.548 |
| fitted (MAP, prior = hand-set) | 0.753 | 0.535 |

## Parameters

| parameter | hand-set | fitted |
|---|---|---|
| relevance_exponent | 1.0 | 0.859 |
| price_exponent | 0.5 | 0.621 |
| quality_p | 1.0 | 0.981 |
| weight: audience_fit | 0.3 | 0.301 |
| weight: persona_fit | 0.35 | 0.341 |
| weight: notes_fit | 0.2 | 0.207 |
| weight: scale | 0.05 | 0.05 |
| weight: seasonality | 0.1 | 0.101 |

## Independent check on the 15 golden examples (never used for fitting)

| | precision | must-include recall@5 |
|---|---|---|
| hand-set | 0.939 | 0.7 |
| fitted | 0.939 | 0.7 |

Top-5 change vs hand-set on golden: 0.061
