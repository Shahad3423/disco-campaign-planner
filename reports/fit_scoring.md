# Fitting the scoring constants from pairwise labels

Labels: order-consistent, decisive LLM-judge pairs (prompts/pairwise_judge.md, both orders asked). Fit on 223 DEV pairs; 252 TEST pairs never used to fit.

## TEST brands (never used to fit): pairwise agreement with the judge

| model | accuracy | log loss |
|---|---|---|
| hand-set config | 0.786 | 0.556 |
| fitted on dev | 0.786 | 0.546 |

## DEV brands: 5-fold CV by advertiser

| model | accuracy | log loss |
|---|---|---|
| hand-set config (scale only fitted) | 0.704 | 0.567 |
| fitted (MAP, prior = hand-set) | 0.705 | 0.56 |

## Parameters

| parameter | hand-set | fitted |
|---|---|---|
| relevance_exponent | 1.0 | 0.893 |
| price_exponent | 0.5 | 0.572 |
| quality_p | 1.0 | 0.987 |
| weight: audience_fit | 0.3 | 0.299 |
| weight: persona_fit | 0.35 | 0.344 |
| weight: notes_fit | 0.2 | 0.207 |
| weight: scale | 0.05 | 0.05 |
| weight: seasonality | 0.1 | 0.1 |

## Independent check on the 15 golden examples (never used for fitting)

| | precision | must-include recall@5 |
|---|---|---|
| hand-set | 0.97 | 0.6 |
| fitted | 0.97 | 0.65 |

Top-5 change vs hand-set on golden: 0.108

## Decision: keep the hand-set values
The dev-only fit lands next to the hand-set values (relevance 0.89 vs 1.0, price 0.57 vs 0.5, p 0.99 vs 1.0,
weights unchanged) and does not improve ranking on the TEST brands (accuracy 0.786 both; log loss 0.556 -> 0.546).
Golden recall +0.05 is one case. Adopting would add false precision, so the config stays hand-set; the result
is evidence that the hand-set region is where the labels point (flat maximum, as the sensitivity analysis
predicted). The judge's own order-consistency is 92%, so ~0.79 agreement is close to what this label source allows.

## Specification lessons (from the earlier grid, fit on all holdout pairs before the split; withdrawn)
- A harsh zero floor (log of 0.001) plus a weak prior pushed the relevance exponent to its bound (0.1):
  lower log loss, worse ranking and golden precision (0.97 -> 0.86). Fixed with floor 0.15 and a strong prior.
- The judge prefers a host with no category link in ~28% of mixed pairs, mostly when ordering two weak hosts;
  next time, sample pairs near the decision boundary.
