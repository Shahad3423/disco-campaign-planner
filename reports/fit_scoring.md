# Fitting the scoring constants from pairwise labels

Labels: 331 order-consistent, decisive LLM-judge pairs over 64 holdout advertisers (prompts/pairwise_judge.md, both orders asked).

## Held-out pairwise agreement with the judge (5-fold CV by advertiser)

| model | accuracy | log loss |
|---|---|---|
| hand-set config (scale only fitted) | 0.774 | 0.516 |
| fitted (MAP, prior = hand-set) | 0.777 | 0.504 |

## Parameters

| parameter | hand-set | fitted |
|---|---|---|
| relevance_exponent | 1.0 | 0.788 |
| price_exponent | 0.5 | 0.607 |
| quality_p | 1.0 | 0.995 |
| weight: audience_fit | 0.3 | 0.298 |
| weight: persona_fit | 0.35 | 0.343 |
| weight: notes_fit | 0.2 | 0.208 |
| weight: scale | 0.05 | 0.05 |
| weight: seasonality | 0.1 | 0.1 |

## Independent check on the 15 golden examples (never used for fitting)

| | precision | must-include recall@5 |
|---|---|---|
| hand-set | 0.97 | 0.6 |
| fitted | 0.97 | 0.65 |

Top-5 change vs hand-set on golden: 0.108

## Specification grid (why EPS = 0.15 and a strong prior)

| zero-floor EPS | prior | CV acc hand-set | CV acc fitted | a | b | p | golden precision / recall (fitted) |
|---|---|---|---|---|---|---|---|
| 0.001 | 2 | 0.756 | 0.711 | 0.10 | 0.81 | 1.51 | 0.856 / 0.60 |
| 0.001 | 50 | 0.756 | 0.730 | 0.10 | 0.60 | 1.05 | 0.856 / 0.60 |
| 0.05 | 2 | 0.756 | 0.752 | 0.13 | 0.41 | 1.47 | 0.886 / 0.60 |
| 0.05 | 50 | 0.756 | 0.760 | 0.81 | 0.63 | 1.00 | 0.970 / 0.65 |
| 0.15 | 2 | 0.774 | 0.746 | 0.21 | 0.37 | 1.18 | 0.917 / 0.70 |
| **0.15** | **50** | 0.774 | **0.777** | 0.79 | 0.61 | 1.00 | **0.970 / 0.65** |

## What we learned
1. **With a sensible specification the labels agree with the hand-set values** (flat maximum, as the
   sensitivity analysis predicted): relevance exponent 1.0 -> 0.8, price exponent 0.5 -> 0.6, p stays 1.
   Adopted, rounded, because they're now label-supported rather than guessed; the gain is within noise.
2. **A weak prior + a harsh zero floor overfits a mis-specified loss:** log loss improves, ranking accuracy
   and golden precision get worse. Optimise and judge on the metric you care about (ordering), not only log loss.
3. **The judge prefers a host with no category link in 28% of the pairs where exactly one has a link**, mostly
   when choosing between two weak hosts (it weighs audience over a weak category match). Some answers are just
   odd (hemp oil for dogs -> a women's apparel host). The judge is a noisy labeller, not ground truth: hence the
   40-pair human sheet (`human_labels_todo.csv`).
4. **Sample pairs near the decision boundary** (last recommended vs first excluded) next time: a third of these
   pairs were random, and those mostly order hosts we'd never recommend.
