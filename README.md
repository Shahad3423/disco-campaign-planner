# Disco Campaign Planner

One sentence from an advertiser → ranked publishers (with exclusions and reasons), 3–5 persona-tuned creatives,
and a launchable campaign config. A second tab simulates serving: one shopper, only **platform + zip + first name**.

**Demo:** https://discocampaignplanner.streamlit.app · **Local:** `pip install -r requirements.txt`, put `GEMINI_API_KEY=…` in `.env`,
`streamlit run app/streamlit_app.py` (no key: the 15 examples run from a committed cache) · `pytest` · `python -m disco_planner.eval.run --creatives --robustness`

## What I built
`brief → intake (LLM ×3, self-consistency) → gate → publisher scoring → personas → creatives (write → review + rule checks → rewrite) → budget → config`.
The LLM handles language on **closed catalog vocabularies**; code does every number and rank, so each score has a visible breakdown.
**score = relevance × affordability^0.5 × quality × P(host accepts)**: multiplicative like pCTR×pCVR, so a perfect audience can't
rescue an irrelevant host; competitors (hosts selling the same thing, which can block us on their thank-you page) are flagged and
discounted. Vague briefs get questions, B2B gets zero publishers, regulated claims ("FDA approved") are blocked in code.
Prompts: `prompts/`. Weights and priors: `config/`. Every decision and rejected alternative: `docs/DECISIONS.md`.

**Config shape:** what billing, serving and an approver each need: pricing (CPC default; CPM for awareness; tCPA only after verified
conversions, since Disco then carries prediction risk), economics (margin → max CAC → target CPA → max/start CPC, assumptions labelled),
allocations (capped by host inventory, minimum learnable spend, 15% exploration), allowed serve-time signals, creatives ↔ personas ↔
hosts, guardrails (claims, stop-loss), measurement (click-through billed, view-through never billed, 10% holdout), open questions.

## Results (blind labels on the 15 examples; 100-brand holdout; `docs/EXPERIMENTS.md`)
| | baseline | now | | now |
|---|---|---|---|---|
| precision of recommended hosts | 0.85 | **0.92** | creative rule / review pass | **100% / 95%** |
| paraphrase stability (top-5 Jaccard) | 0.71 | **0.84** | unsupported claims in copy | **0** |
| must-exclude violations | 2 | **1** | prompt-injection cases | **4/4** |

_HOLDOUT_

## Hard vs easy
**Easy:** LLM calls, schemas, UI. **Hard:** (1) *matching with no feedback*: ranking is a cold-start prior, so the work is making it
honest (weak-fit detection, competitors, "no publishers") and measurable; (2) *LLM variance*: rewording a brief swapped a third of
the top-5 until self-consistency, and ablations were needed to separate prompt from sampling effects; (3) *weak serve-time signals that
compound*: billing vs shipping zip changes who you target; features carry confidence, are tempered into a Bayesian update of the host
prior, and never trigger hard rules. The ablation shows the first name changes *which variant*, never *which campaign*.

## Next week
Learned pCTR/pCVR from logs (this scorer becomes the prior) · Thompson-sampling bandit over variants · retrieval (embeddings) before
ranking for 1,000+ hosts · human-labelled eval set with agreement · Census income per zip · pacing service · prompt evals as CI gates.

## Cut, on purpose
Auction/pacing simulation (no data to make it honest) · image creative · auth · LLM re-ranking of hosts (variance and cost; evals
didn't show a need) · ethnicity inference from names (sensitive, unnecessary).
