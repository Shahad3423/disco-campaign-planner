# Disco Campaign Planner

One sentence from an advertiser → ranked publishers (with exclusions and reasons), 3–5 persona-tuned creatives, and a
campaign config Disco could run: hosts, budget split, bids, guardrails, measurement.

**Demo:** https://discocampaignplanner.streamlit.app (15 examples load instantly from a committed cache; type any brief).
**Local:** `pip install -r requirements.txt` · `GEMINI_API_KEY=…` in `.env` (optional: without it the examples run from cache)
· `streamlit run app/streamlit_app.py` · `pytest` (217 tests) · evals: `python -m disco_planner.eval.run`, `…eval.bidding_eval`.

## What I built
**LLM for language, code for numbers.** Intake (Gemini, 3 samples in parallel, majority-merged) turns the brief into a typed
profile on the catalog's closed vocabularies; everything after that is deterministic Python with every constant in `config/`.
- **Hosts:** `relevance × affordability^0.5 × quality`, multiplicative like pCTR × pCVR, so a perfect audience can't rescue an
  irrelevant host. Unknown inputs are left out, not guessed. Competitors are flagged (a host setting decides at serving), vague
  briefs get questions, B2B gets no hosts, regulated claims are blocked in code. Each reason is plain English, from templates.
- **Personas → creatives:** 3–5 personas by interest × price × audience fit. Per persona, in parallel: 6 candidates → a separate
  critic + code rule checks → keep 2 → one rewrite. Writer and critic share one fact sheet; a line the critic fails never ships as
  approved.
- **Money:** prices come from a simulated one-slot second-price auction against 115 other campaigns, so diminishing returns are
  the market's, not an assumed curve. The budget split is a linear program solved exactly (cheapest unit first, partial last
  bucket), with a forecast range and a reason for every bid.
- **Config shape:** what billing, serving and an approver each need: pricing (CPC; CPM for reach; tCPA only after 50 tracked
  conversions), budget per host with start/max bid, a phased bid strategy, creatives per persona, guardrails (allowed claims,
  competitor opt-ins, max CPC), measurement (click-through billed, 10% holdout), open questions. Shape and rationale: D2, D6.
- **Serving** (code + tests, not on the page): host + zip + first name → features with confidences → Bayesian persona posterior →
  eCPM auction, in milliseconds, no LLM. **Speed:** the plan shows after intake (~13 s); creatives stream in after (~45 s).

Prompts: `prompts/`. Architecture, engineering decisions and the production path: `docs/ARCHITECTURE.md`. Every decision and
rejected alternative: `docs/DECISIONS.md` (D1–D36). The score with a worked example: `docs/SCORING.md` §0.

## Results (`docs/EXPERIMENTS.md`, `reports/`)
| hosts (15 briefs, labels written blind) | | creatives (fresh run, 44 personas) | | money (simulated market) | |
|---|---|---|---|---|---|
| precision of recommended hosts | **0.91** | personas with a passing line | 43% → **73%** | split vs LP optimum | **exact** |
| must-include recall@5 | 0.77 | critic: claims OK | 88% → **94%** | forecast range covers replay | 79% |
| must-exclude violations | 1 | prompt-injection cases | 4/4 | budget spent | **100%** |
| 49 unseen brands: LLM judge agrees | 89.7% | paraphrase stability (top-5) | 0.71 → 0.84 | clearing CPC in benchmark | 20/20 |

## Hard vs easy, and where the interesting work is
**Easy:** calling an LLM, schemas, the UI. **Hard:** (1) *matching with no feedback*: the ranking is a cold-start prior, so the
work is making it honest (exclusions with reasons, weak-fit flags, competitors) and measurable: blind golden labels, a 49/51
dev/test holdout where test is touched once per decision, an LLM judge, sensitivity analysis; most rule ideas I tried lost on
these and are documented as rejected. (2) *LLM variance and grounding*: one rewording swapped a third of the top-5 until
self-consistency; copy stays honest only if writer and critic see the same facts. (3) *money with no market data*: prices from
competition, an exact optimiser, and ranges instead of point forecasts. (4) *weak serve-time signals that compound*: billing vs
shipping zip changes who you target, so features carry confidence and never trigger hard rules.

## Next week
Learned pCTR/pCVR from logs, with this scorer as the prior · a bandit over creative variants · embedding retrieval before ranking
for 1,000+ hosts · a pacing service against daily caps · 40 human labels to check the LLM judge · creatives only after a vague brief
is clarified · aggressiveness as a CPC allowance above the start bid · prompt evals as CI gates.

## Cut, on purpose
Learned models and real pacing (no outcome data to fit them honestly) · LLM re-ranking of hosts (variance and cost; the
deterministic scorer won on evals) · image creatives · auth and multi-tenant storage · the serving simulator and eval pages (the
demo is the planner; serving and evals live in code, tests and `reports/`).
