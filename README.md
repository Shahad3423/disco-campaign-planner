# Disco Campaign Planner

An advertiser types a sentence; the system returns ranked publishers (with exclusions and reasons),
3–5 persona-tuned creatives, and a launchable campaign config. A second tab simulates Disco's
serving step: one shopper, only **platform + zip + first name**, picks and personalises the offer.

**Live demo:** _LINK_ · **Run locally:** `python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && echo GEMINI_API_KEY=… > .env && .venv/bin/streamlit run app/streamlit_app.py` (no key? the 15 examples run from a committed cache). Tests: `pytest`. Evals: `python -m disco_planner.eval.run [--creatives --robustness]`.

## How it works
`brief → intake (LLM ×3, self-consistency) → gate → publisher scoring (code) → personas (code) → creatives (LLM write → LLM review + rule checks → 1 rewrite) → economics, bids, budget (code) → CampaignConfig`.
The LLM maps language onto the catalog's **closed vocabularies**; deterministic code does every number and rank,
so each score has a visible breakdown and nothing is invented. Publisher score = **relevance × affordability^0.5 ×
quality** (audience, persona two-hop, notes, scale, seasonality) × competitor penalty — multiplicative like pCTR×pCVR,
because a perfect audience can't rescue an irrelevant or unaffordable host. All prompts are in `prompts/`; all
weights and priors in `config/`. Every decision and its alternatives: `docs/DECISIONS.md`.

## Why the campaign config looks like this
Disco is a post-purchase network: hosts are other brands' thank-you pages, advertisers pay per click.
So the config carries what billing, serving and a human approver each need: **pricing** (CPC default; CPM for
awareness; tCPA only after verified conversions, because Disco would carry the prediction risk), **economics**
(max CAC → target CPA → max CPC, with every assumption labelled), **allocations** (score-weighted, capped by
host inventory, minimum learnable spend per host, 15% exploration, start bid at 70% of break-even),
**targeting** incl. which serve-time signals are allowed and how, **creatives** mapped to persona and hosts,
**guardrails** (allowed claims, competitor hosts needing approval, stop-loss), **measurement** (click-through
billed, view-through reported never billed, 10% holdout), and **open questions** when confidence is low.

## Results (blind golden labels for the 15 examples; `docs/EXPERIMENTS.md`)
_RESULTS_

## Hard vs easy
**Easy:** calling an LLM, JSON schemas, a UI. **Hard, and where the work lives:** (1) *matching without
feedback*: no clicks exist, so ranking is a cold-start prior; the real job is making it honest (weak-fit
detection, competitor conflicts, saying "no publishers" to B2B) and measurable. (2) *LLM variance*: rewording a
brief swapped a third of the top-5 until self-consistency; evals had to separate prompt effects from sampling
effects. (3) *Weak serve-time signals that compound*: names and zips are noisy (billing vs shipping zip changes
who you're targeting); every inferred feature carries confidence, is tempered into a Bayesian update of the
host prior, and never triggers a hard rule. The ablation shows the name changes *which variant*, never *which
campaign*. (4) *Copy that's persona-specific without inventing claims*: a separate reviewer plus code checks.

## Next week
Learned pCTR/pCVR from logs replacing hand weights (this scorer becomes the cold-start prior) · Thompson-sampling
bandit over variants with this posterior as prior · embedding similarity as a second relevance signal ·
human-labelled set larger than 15 with inter-rater agreement · Census income per zip (key pending) · pacing
service + real budget counters · prompt versioning in CI with eval gates.

## Cut, on purpose
Real auction/pacing simulation (no data to make it honest) · image creative · auth/multi-tenant · LLM
re-ranking of publishers (adds cost and variance; the eval didn't show a need) · inferring ethnicity from
names (sensitive under CPRA, and not needed for the decision).
