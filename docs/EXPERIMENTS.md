# Matching experiments

Each change was evaluated against the blind golden set (15 advertisers) before keeping it.
Caveat: 15 cases, so a change of ~0.1 in recall is 1-2 cases and within run-to-run LLM noise;
we treat paraphrase stability and violations as the primary signals and avoid tuning to single cases.

| run | change | gate accuracy | must include recall@5 | must exclude violations | precision | competitor flag recall | implausible personas selected | paraphrase stability |
|---|---|---|---|---|---|---|---|---|
| baseline | additive score, single intake call | 0.867 | 0.8 | 2 | 0.848 | 0.875 | 5 | 0.71 |
| v2a_hierarchy_price | tag hierarchy + price as multiplier (bug: weak specific < broad) | 0.867 | 0.5 | 2 | 0.879 | 0.875 | 5 | - |
| v2b | fix: base = max(specific, capped broad); lower absolute floor | 0.867 | 0.8 | 2 | 0.896 | 0.875 | 5 | - |
| v2c_persona_weakfit | + persona two-hop quality term, weak-network-fit rule | 0.867 | 0.767 | 2 | 0.917 | 0.875 | 5 | - |
| v2d | weak fit also requires price_fit >= 0.4 | 0.867 | 0.667 | 2 | 0.889 | 0.875 | 5 | - |
| v2e_prompt_only_n1 | ablation: intake prompt v2 alone (1 sample) | 0.733 | 0.667 | 1 | 0.944 | 0.75 | 4 | - |
| v2e_selfconsistency | prompt v2 + 3-sample self-consistency (majority vote) | 0.8 | 0.65 | 1 | 0.944 | 0.625 | 4 | 0.938 |
| v2f | prompt v3 + competitors flagged if ANY sample says so | 0.867 | 0.6 | 2 | 0.879 | 0.875 | 5 | 0.835 |
| v2g_soft_competitor | competitor penalty scaled by sample agreement  <- current | 0.867 | 0.7 | 1 | 0.939 | 0.875 | 5 | 0.835 |

## What we learned
- **Additive scoring lets irrelevant hosts win** on neutral price/audience points (meal kits ~ pet hosts for dog food). Relevance and affordability now multiply, like pCTR x pCVR.
- **Broad tags are a trap**: `apparel` matched every apparel host. Category-only matches are capped; specific subcategories carry the match.
- **Two changes at once hide causes**: v2e changed prompt and sampling together; the n=1 ablation showed the prompt's counter-example ("dog food vs dog toys") broke the cat-box competitor flag.
- **Majority voting is wrong for asymmetric errors**: missing a competitor costs budget; a false flag costs an approval request. Final: flag on any sample, penalise by agreement share.
- **Self-consistency buys stability**: paraphrase top-5 Jaccard 0.71 -> 0.84-0.94, at 3x intake cost (cached).
- **Evals with creatives found claim laundering**: "only use stated claims" let an advertiser state "clinically proven weight loss, FDA approved" and get it into copy, and the LLM reviewer agreed. Fix: `config/claims_policy.yaml`, enforced in code at intake and in creative rule checks. After: injection 4/4, unsupported claims 0, creative review pass 95% (v3_full).
- **Serving ablation**: removing the first name changes ~32% of decisions, all of them the creative *variant*, none the *campaign*; unisex names (low confidence) change 8%. A first version without a switch margin flipped on near-ties (42%).
- **v3b (post-review fixes)**: competitor penalty unified with the budget's approval probability (expected value = score x P(host accepts)), so a competitor no longer outranks an equally good non-competitor (dog food: Tailcrate now first; bedding: Hearthstone ahead of Northbed). Persona minimum filled from host presence (plausible-persona recall 0.75 -> 0.83). Precision 0.94 -> 0.92 (one host in one case).
- **Remaining misses are mostly judgment calls** in the golden set (e.g. Marlowe & Co for linen bedding, Pantrygood for an NA drink); we did not tune to them.


## Holdout (100 real brands, written blind) — partial
Free-tier quota ran out after 28 briefs (all in-catalog). On those: 28/28 gates match the writer's
expectation; weak-fit fired for Litter-Robot (~$700) and Quince. Two findings, both fixed or queued:
- **The LLM over-filled `claims_needing_substantiation`** ("self-cleaning", "fair trade", "bond repair"),
  silently removing legitimate selling points from copy. The field is now code-owned: only
  `config/claims_policy.yaml` patterns block; everything else returns to the allowed claims (tested).
- **Apparel hosts appear in ~40% of plans** (13 of the 28 briefs are apparel) and some briefs that state
  "women 22-40" still get a 50-70 host in a 6-host list; to judge on the full 100 before changing the cap.
