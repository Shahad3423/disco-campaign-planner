# Bidding and allocation evaluation (simulated market)

## 1. Realism

Median clearing CPC inside the benchmark range ($0.45-$1.81) on 20/20 hosts (CTR 3.0%; implied CPM = CPC x CTR x 1000).

| host | rival campaigns | p25 | median | p75 | implied CPM | median in range |
|---|---|---|---|---|---|---|
| Swiftcart | 22 | $0.78 | $0.89 | $1.04 | $27 | yes |
| Movewell | 20 | $0.81 | $0.93 | $1.11 | $28 | yes |
| Studiogrid | 11 | $0.70 | $0.81 | $0.98 | $24 | yes |
| Marlowe & Co. | 13 | $0.63 | $0.73 | $0.86 | $22 | yes |
| Linden Park | 9 | $0.67 | $0.78 | $0.91 | $23 | yes |
| Everbody | 14 | $0.54 | $0.64 | $0.76 | $19 | yes |
| Pawline | 10 | $0.83 | $0.96 | $1.13 | $29 | yes |
| Pantrygood | 18 | $0.79 | $0.92 | $1.07 | $28 | yes |
| Ruffco | 10 | $0.77 | $0.88 | $1.03 | $26 | yes |
| Heartfoot | 17 | $0.57 | $0.65 | $0.75 | $20 | yes |
| Northbed | 20 | $1.24 | $1.42 | $1.66 | $43 | yes |
| Daily Form | 24 | $1.14 | $1.30 | $1.52 | $39 | yes |
| Velvetline | 10 | $0.89 | $1.02 | $1.19 | $31 | yes |
| Hearthstone Goods | 22 | $1.20 | $1.37 | $1.57 | $41 | yes |
| Kitchenly | 16 | $0.74 | $0.86 | $1.03 | $26 | yes |
| Stride & Stem | 17 | $0.54 | $0.63 | $0.75 | $19 | yes |
| Cloudfoot | 21 | $0.72 | $0.83 | $0.97 | $25 | yes |
| Tailcrate | 11 | $0.80 | $0.94 | $1.12 | $28 | yes |
| Strandlab | 11 | $0.88 | $1.01 | $1.20 | $30 | yes |
| Pop & Sip | 11 | $0.87 | $1.01 | $1.19 | $30 | yes |

## 2. Forecast back-test

200 replays per plan with fresh rival bids, a market-wide price shift, uncertain true CTR/CVR and click noise. Realised outcome inside the forecast range: **79%** on average (target >= 80%). Replays over budget: 0 (pacing stops at the cap).

| plan | forecast range | median realised | inside range |
|---|---|---|---|
| #1 conversions | 233-840 | 499 | 80% |
| #1 clicks | 7,057-17,644 | 12,771 | 85% |
| #2 conversions | 214-768 | 518 | 89% |
| #2 clicks | 7,140-17,851 | 12,912 | 86% |
| #3 conversions | 280-1,010 | 472 | 67% |
| #3 clicks | 6,011-15,028 | 10,736 | 66% |
| #4 conversions | 73-264 | 171 | 90% |
| #4 clicks | 4,188-10,470 | 7,528 | 83% |
| #6 conversions | 221-796 | 496 | 91% |
| #6 clicks | 7,548-18,871 | 13,516 | 86% |
| #9 conversions | 128-461 | 249 | 72% |
| #9 clicks | 7,322-18,304 | 13,218 | 76% |
| #10 conversions | 250-902 | 537 | 82% |
| #10 clicks | 8,532-21,331 | 15,179 | 82% |
| #11 conversions | 310-1,116 | 549 | 70% |
| #11 clicks | 6,640-16,599 | 11,852 | 72% |
| #12 conversions | 249-897 | 500 | 69% |
| #12 clicks | 7,767-19,417 | 13,921 | 74% |
| #13 conversions | 285-1,027 | 611 | 88% |
| #13 clicks | 7,332-18,330 | 13,135 | 91% |
| #14 conversions | 72-260 | 118 | 71% |
| #14 clicks | 4,132-10,331 | 7,373 | 74% |

## 3. Allocation vs baselines (forecast units, as % of ours without the share cap)

| plan | ours | ours, no cap | equal split | score-proportional | all on best host |
|---|---|---|---|---|---|
| #1 conversions | 100% | 100% | 61% | 86% | 89% |
| #1 clicks | 100% | 100% | 50% | 83% | 92% |
| #2 conversions | 100% | 100% | 79% | 83% | 97% |
| #2 clicks | 100% | 100% | 68% | 73% | 99% |
| #3 conversions | 100% | 100% | 57% | 80% | 100% |
| #3 clicks | 100% | 100% | 38% | 68% | 100% |
| #4 conversions | 100% | 100% | 95% | 96% | 98% |
| #4 clicks | 100% | 100% | 76% | 85% | 100% |
| #6 conversions | 100% | 100% | 83% | 87% | 99% |
| #6 clicks | 100% | 100% | 72% | 78% | 100% |
| #9 conversions | 100% | 100% | 43% | 63% | 100% |
| #9 clicks | 100% | 100% | 31% | 56% | 100% |
| #10 conversions | 100% | 100% | 78% | 86% | 99% |
| #10 clicks | 100% | 100% | 69% | 79% | 100% |
| #11 conversions | 100% | 100% | 62% | 78% | 100% |
| #11 clicks | 100% | 100% | 49% | 68% | 100% |
| #12 conversions | 100% | 100% | 62% | 80% | 100% |
| #12 clicks | 100% | 100% | 51% | 75% | 100% |
| #13 conversions | 100% | 100% | 66% | 88% | 92% |
| #13 clicks | 100% | 100% | 60% | 88% | 94% |
| #14 conversions | 100% | 100% | 71% | 88% | 100% |
| #14 clicks | 100% | 100% | 48% | 79% | 100% |

## 4. Honest bidding (utility = value - price, summed over won page views)

| plan | bid 0.5x value | 0.75x | 1.0x (true value) | 1.25x | 1.5x | truthful is best |
|---|---|---|---|---|---|---|
| #1 | 332 | 2,256 | 2,834 | 2,378 | 1,432 | yes |
| #2 | 34 | 1,014 | 1,893 | 1,282 | 266 | yes |
| #3 | 40 | 1,024 | 1,846 | 1,287 | 1 | yes |
| #4 | 6 | 595 | 1,235 | 745 | 31 | yes |
| #6 | 1 | 474 | 1,099 | 675 | 120 | yes |
| #9 | 1,533 | 2,937 | 3,757 | 2,985 | 1,754 | yes |
| #10 | 2 | 217 | 558 | 328 | -62 | yes |
| #11 | 9 | 644 | 1,189 | 858 | 21 | yes |
| #12 | 19 | 854 | 1,476 | 1,034 | 176 | yes |
| #13 | 82 | 1,456 | 2,084 | 1,784 | 1,084 | yes |
| #14 | 2 | 554 | 1,392 | 771 | 4 | yes |
