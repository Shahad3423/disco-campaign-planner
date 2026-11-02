# Architecture: what runs where, why it is built this way, and what changes in production

## 1. The system in one picture

```
                    ┌──────────────────── PLANNING (per advertiser, seconds, uses the LLM) ─────────────────────┐
 advertiser brief ─►│ 1 INTAKE (LLM x3 in parallel, majority merge) ─► gate: proceed / clarify / off-network /   │
 + optional inputs  │   AdvertiserProfile (closed vocabularies)          reject                                  │
 (budget, goal,     │ 2 HOST SCORING (code) ─► ranked hosts, exclusions, plain-English reasons                   │
  bidding)          │ 3 PERSONAS (code) ─► 3-5 shopper types to write for                                       │
                    │ 4 MARKET + BUDGET (code) ─► bid landscape per host ─► exact-optimum split ─► forecast, bids │
                    │ 5 CREATIVES (LLM, per persona in parallel: write ─► critic + rule checks ─► rewrite)       │
                    │   ─► CampaignConfig (JSON): hosts, budget, bids, creatives, guardrails, measurement        │
                    └────────────────────────────────────────────────┬──────────────────────────────────────────┘
                                                                     │ approved config
                    ┌──────────────────── SERVING (per shopper, milliseconds, no LLM) ▼─────────────────────────┐
 {host, zip,        │ features (name -> gender/age, zip -> region/urbanicity/income, time) with confidences ─►   │
  first_name} ─────►│ persona posterior (host prior x tempered likelihoods) ─► eligible campaigns (budget left) │
                    │ ─► bid x pCTR = eCPM auction (one slot, second price) ─► variant ─► render name safely      │
                    └────────────────────────────────────────────────────────────────────────────────────────────┘
```

| | Planning | Serving |
|---|---|---|
| Runs | once per campaign | once per page view |
| Latency budget | seconds (UI shows the plan after intake, creatives stream in after) | milliseconds |
| LLM | intake (3 calls) and creatives (about 3 per persona) | none: creatives are pre-written templates |
| Code | every number: scores, ranks, prices, budget, bids | features, posterior, auction |

## 2. The components, top down

| # | Component | Files | In | Out | How |
|---|---|---|---|---|---|
| 1 | Intake | `planning/intake.py`, `prompts/intake.md` | brief | `AdvertiserProfile` | LLM, 3 samples, majority vote; tags forced onto the catalog's vocabulary in code; regulated claims moved out by `claims.py` |
| 2 | Host scoring | `planning/publisher_scoring.py`, `planning/explain.py` | profile | ranked `PublisherScore`s | `relevance x price^0.5 x quality`, unknown parts left out; cutoffs; 5% exploration hosts; templated reasons |
| 3 | Personas | `planning/personas.py` | profile, scores | `PersonaMatch`es | `affinity x price_compat x audience_compat`; stated-fact conflicts exclude |
| 4 | Market | `planning/market.py` | all stored plans | bid landscape per host | rival bids from 115 plans, log-normal per view, one-slot second price, calibrated to a $0.90 median |
| 5 | Budget and bids | `planning/budget.py` | landscapes, inputs | `BudgetPlan`, `BidStrategy` | cheapest-unit-first with partial buckets = exact LP optimum; forecast ranges; bid phases; `bid_reason` per host |
| 6 | Creatives | `planning/creative.py`, `prompts/creative_*.md` | profile, personas | variants + reviews | per persona in parallel: 6 candidates -> critic + code rule checks -> keep 2 -> one rewrite; failed lines never ship silently |
| 7 | Orchestration | `planning/pipeline.py` | brief, inputs | `CampaignPlan` | `run` = intake + `plan_from_profile` (code only) + `add_creatives` |
| 8 | Serving | `serving/features.py`, `geo.py`, `household.py`, `calendar.py`, `decision.py`, `insights.py` | host, zip, first name | offers | features with confidence -> Bayesian persona posterior -> eCPM auction; auction insights without revealing rival bids |
| 9 | LLM access | `llm/provider.py`, `llm/prompts.py` | prompt, schema | validated object | one interface; Vertex -> AI Studio -> model fallbacks; JSON validated against the schema, one retry with the error; disk cache |
| 10 | App | `app/streamlit_app.py`, `app/components.py` | UI inputs | the page | two-phase render: plan first, creatives after |
| 11 | Evals | `eval/*.py`, `tests/` | golden, holdout, labels | reports | golden labels (blind), 49/51 dev/test holdout, pairwise LLM judge, sensitivity, bidding back-test, serving ablation |

## 3. Engineering decisions, and why

**LLM for language, code for numbers.** The LLM reads the brief and writes copy; it never ranks, prices or allocates. It
answers on closed vocabularies (the catalog's tags and persona ids) and code drops anything off-vocabulary, so the output
can be checked, tested and explained. Every number on the page has a formula and a config entry (`config/*.yaml`).

**Typed contracts between stages.** Every stage hands the next a Pydantic model (`domain/models.py`). LLM output is
validated against the same schema; on failure the call is retried once with the validation error in the prompt. A stage
can be tested, cached or replaced on its own, and stored plans stay loadable when a field is removed (extras are ignored).

**Self-consistency for intake, in parallel.** One sample at temperature 0 plus two at 0.8, merged by majority (a tag must
appear in 2 of 3; weights averaged; the most cautious gate wins ties). Rewording a brief used to swap a third of the
top-5 hosts; with 3 samples paraphrase stability went from 0.71 to 0.84 (top-5 Jaccard). The samples are independent, so
they run in a thread pool: intake went from 40.8 s to 13.5 s. Threads don't inherit Python context variables, so each
task runs in a copy of the caller's context (the app's live/cache toggle and the call counter keep working), and the
counter has a lock.

**The plan doesn't wait for the copy.** Creatives are the slowest stage and nothing else depends on them.
`pipeline.run(with_creatives=False)` returns the full plan, the app renders it with a placeholder, then
`add_creatives` fills the slot. A fresh brief shows hosts, budget and bids after intake (~13 s).

**Creatives per persona, in parallel, with a separate critic.** Each persona gets its own cycle: write 6 candidates with
different hooks -> a critic (a separate call that sees only the output) scores punch, persona fit and claims -> code rule
checks (length, banned words, regulated claims, demographic references) -> keep the best 2 -> one rewrite of failures.
Writer and critic share one fact sheet: the stated claims plus offer terms the brief states (D35). Per-persona calls
produce less text each and no persona waits for another's review: 54 s -> 28-37 s with 4 candidates (48 s with 6,
adopted in D35 because the plan no longer waits for them).

**A disk cache keyed on (model, prompt, input, temperature).** Evals are reproducible, a prompt change re-runs only what
it touches, and the hosted demo works without a key. A "live" run bypasses the cache and never writes to it.

**One provider interface with fallbacks.** Gemini through Vertex first, AI Studio second, and a model chain for 429/503;
`FakeProvider` for tests. Prompts are markdown files in `prompts/` (the single source of truth), rendered at runtime.

**A market, not a price guess.** Advertiser prices come from a simulated one-slot second-price auction against the other
115 known campaigns, so diminishing returns come from competition, not an assumed curve. The allocation is a linear
program (each host's cost per unit only rises), solved exactly by buying the cheapest unit first with a partial last
bucket; tests check it against an LP solver.

**Plain-English explanations from templates, not an LLM.** Reasons are built from the score components by fixed
templates: instant, free, testable, and they cannot invent a reason. A test checks every reason on 115 brands.

**Serving has no LLM.** A request carries a host id, a zip code and a first name. Each feature carries a confidence;
likelihoods are tempered by it in a Bayesian update of the host's persona prior; no feature triggers a hard rule.

## 4. Production: what evolves and how

| Component | Prototype | Production |
|---|---|---|
| Intake | 3 Gemini calls per brief, disk cache | async job per campaign; prompt versions with CI eval gates; cache in a shared store keyed on input hash; cheaper model for easy briefs |
| Host scoring | hand-set log-linear scorer | learned pCTR/pCVR (gradient-boosted trees, then two-tower retrieval for 1,000+ hosts); this scorer becomes the cold-start prior and a feature |
| Market | rivals simulated from 115 plans | the bid landscape per host read from Disco's own auction logs (every bid and clearing price); real reserve and increment |
| Budget | exact LP over prior CTR/CVR | the same optimiser over learned, per-campaign rates; re-solved daily as rates and prices move |
| Pacing | even daily pacing; the flight budget is enforced | a pacing service (throttling or bid shading) against daily caps, fed by delivery logs |
| Bidding | phases: learning -> maximise -> tCPA after 50 conversions | auto-bidding on conversion feedback; learning-volume checks; tCPA / tROAS once tracking is verified |
| Creatives | LLM-written, critic-checked, 2 per persona | a human approval queue; a Thompson-sampling bandit across variants per host; brand-safety review |
| Personas | 10 hand-written personas | latent shopper embeddings; personas stay as human-readable labels and a creative brief |
| Serving | Python function, static lookups | stateless service behind the checkout extension; in-memory feature tables; budget counters in a low-latency store; fallback creative on timeouts; p99 latency SLO |
| Measurement | golden set, holdout, LLM judge, back-tests | per-advertiser holdout groups for incremental lift; calibration dashboards; alerts on feature drift (e.g. billing vs shipping zip) |
| LLM ops | call counter in the UI | cost and latency per call, per prompt version; rate-limit budgets; PII rules for first names |
