# Bidding and allocation evaluation (simulated market)

## 1. Realism

Median clearing CPC inside the benchmark range ($0.45-$1.81) on 20/20 hosts (CTR 3.0%; implied CPM = CPC x CTR x 1000).

| host | rival campaigns | p25 | median | p75 | implied CPM | median in range |
|---|---|---|---|---|---|---|
| Swiftcart | 22 | $0.74 | $0.85 | $1.00 | $26 | yes |
| Movewell | 20 | $0.81 | $0.94 | $1.09 | $28 | yes |
| Studiogrid | 12 | $0.70 | $0.84 | $1.02 | $25 | yes |
| Marlowe & Co. | 13 | $0.65 | $0.74 | $0.88 | $22 | yes |
| Linden Park | 9 | $0.69 | $0.78 | $0.92 | $23 | yes |
| Everbody | 14 | $0.52 | $0.61 | $0.73 | $18 | yes |
| Pawline | 10 | $0.81 | $0.95 | $1.11 | $29 | yes |
| Pantrygood | 18 | $0.85 | $0.96 | $1.10 | $29 | yes |
| Ruffco | 10 | $0.74 | $0.84 | $0.96 | $25 | yes |
| Heartfoot | 17 | $0.64 | $0.77 | $0.89 | $23 | yes |
| Northbed | 20 | $1.18 | $1.39 | $1.61 | $42 | yes |
| Daily Form | 24 | $1.09 | $1.24 | $1.44 | $37 | yes |
| Velvetline | 10 | $0.86 | $1.00 | $1.14 | $30 | yes |
| Hearthstone Goods | 22 | $1.14 | $1.30 | $1.51 | $39 | yes |
| Kitchenly | 16 | $0.77 | $0.90 | $1.07 | $27 | yes |
| Stride & Stem | 17 | $0.57 | $0.65 | $0.75 | $19 | yes |
| Cloudfoot | 21 | $0.75 | $0.86 | $1.00 | $26 | yes |
| Tailcrate | 11 | $0.79 | $0.91 | $1.08 | $27 | yes |
| Strandlab | 11 | $0.86 | $0.97 | $1.16 | $29 | yes |
| Pop & Sip | 11 | $0.83 | $0.97 | $1.14 | $29 | yes |

## 2. Forecast back-test

200 replays per plan with fresh rival bids, a market-wide price shift, uncertain true CTR/CVR and click noise. Realised outcome inside the forecast range: **77%** on average (target >= 80%). Replays over budget: 0 (pacing stops at the cap).

| plan | forecast range | median realised | inside range |
|---|---|---|---|
| #1 conversions | 220-792 | 439 | 76% |
| #1 clicks | 6,653-16,632 | 11,872 | 68% |
| #2 conversions | 225-809 | 425 | 76% |
| #2 clicks | 7,656-19,140 | 13,211 | 78% |
| #3 conversions | 290-1,046 | 483 | 66% |
| #3 clicks | 6,224-15,561 | 10,903 | 69% |
| #4 conversions | 80-288 | 168 | 92% |
| #4 clicks | 4,426-11,066 | 7,795 | 74% |
| #6 conversions | 222-798 | 455 | 86% |
| #6 clicks | 7,525-18,811 | 13,191 | 88% |
| #9 conversions | 108-389 | 211 | 60% |
| #9 clicks | 5,950-14,875 | 10,347 | 68% |
| #10 conversions | 266-958 | 621 | 94% |
| #10 clicks | 8,908-22,271 | 15,719 | 92% |
| #11 conversions | 190-684 | 416 | 87% |
| #11 clicks | 4,763-11,907 | 8,462 | 72% |
| #12 conversions | 251-902 | 473 | 76% |
| #12 clicks | 7,812-19,530 | 13,635 | 75% |
| #13 conversions | 181-650 | 337 | 69% |
| #13 clicks | 4,763-11,907 | 8,178 | 72% |
| #14 conversions | 75-272 | 140 | 70% |
| #14 clicks | 4,309-10,773 | 7,620 | 73% |

## 3. Allocation vs baselines (forecast units, as % of ours without the share cap)

| plan | ours | ours, no cap | equal split | score-proportional | all on best host |
|---|---|---|---|---|---|
| #1 conversions | 100% | 100% | 49% | 51% | 97% |
| #1 clicks | 100% | 100% | 45% | 50% | 100% |
| #2 conversions | 100% | 100% | 48% | 65% | 90% |
| #2 clicks | 100% | 100% | 36% | 56% | 91% |
| #3 conversions | 100% | 100% | 70% | 78% | 100% |
| #3 clicks | 100% | 100% | 64% | 75% | 100% |
| #4 conversions | 100% | 100% | 91% | 89% | 96% |
| #4 clicks | 100% | 100% | 84% | 85% | 100% |
| #6 conversions | 100% | 100% | 74% | 90% | 91% |
| #6 clicks | 100% | 100% | 69% | 86% | 92% |
| #9 conversions | 100% | 100% | 21% | 14% | 86% |
| #9 clicks | 100% | 100% | 16% | 11% | 89% |
| #10 conversions | 100% | 100% | 96% | 97% | 94% |
| #10 clicks | 100% | 100% | 96% | 96% | 96% |
| #11 conversions | 100% | 100% | 95% | 92% | 95% |
| #11 clicks | 100% | 100% | 87% | 89% | 100% |
| #12 conversions | 100% | 100% | 49% | 49% | 100% |
| #12 clicks | 100% | 100% | 48% | 48% | 100% |
| #13 conversions | 100% | 100% | 71% | 79% | 100% |
| #13 clicks | 100% | 100% | 53% | 67% | 100% |
| #14 conversions | 100% | 100% | 76% | 88% | 100% |
| #14 clicks | 100% | 100% | 67% | 83% | 100% |

## 4. Honest bidding (utility = value - price, summed over won page views)

| plan | bid 0.5x value | 0.75x | 1.0x (true value) | 1.25x | 1.5x | truthful is best |
|---|---|---|---|---|---|---|
| #1 | 0 | 24 | 54 | 25 | -26 | yes |
| #2 | 0 | 50 | 116 | 63 | -22 | yes |
| #3 | 16 | 97 | 118 | 97 | 53 | yes |
| #4 | 1 | 41 | 87 | 46 | -4 | yes |
| #6 | 0 | 16 | 51 | 27 | -7 | yes |
| #9 | 11 | 105 | 120 | 101 | 61 | yes |
| #10 | 0 | 24 | 54 | 35 | 10 | yes |
| #11 | 0 | 22 | 53 | 32 | 3 | yes |
| #12 | 0 | 42 | 85 | 63 | 34 | yes |
| #13 | 0 | 52 | 124 | 71 | 12 | yes |
| #14 | 0 | 50 | 91 | 56 | 10 | yes |
