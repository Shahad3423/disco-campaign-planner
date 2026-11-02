# Disco Campaign Planner

One sentence from an advertiser → ranked publishers (with exclusions and reasons), 3–5 persona-tuned creatives, and a
campaign config Disco could run: hosts, budget split, bids, guardrails, measurement.

**Demo:** https://discocampaignplanner.streamlit.app (15 examples load instantly from a committed cache; type any brief).
**Local:** `pip install -r requirements.txt` · `GEMINI_API_KEY=…` in `.env` (optional: without it the examples run from cache)
· `streamlit run app/streamlit_app.py` · `pytest` (218 tests) · evals: `python -m disco_planner.eval.run`, `…eval.bidding_eval`.

## What I built
**LLM for language, code for numbers.** Intake (Gemini, 3 samples in parallel, majority-merged) turns the brief into a typed
profile on the catalog's closed vocabularies; everything after that is deterministic Python with every constant in `config/`.
- **Hosts:** `relevance × affordability^0.5 × quality`, multiplicative like pCTR × pCVR, so a perfect audience can't rescue an
  irrelevant host. Unknown inputs are left out, not guessed. Competitors are flagged (a host setting decides at serving), vague
  briefs get questions, B2B gets no hosts, regulated claims are blocked in code. Each reason is plain English, from templates.
- **Personas → creatives:** 3–5 personas by interest × price × audience fit; per persona, in parallel: 6 candidates → a separate
  critic + code rule checks → keep 2 → one rewrite. Writer and critic share one fact sheet; failed lines never ship as approved.
- **Money:** prices come from a simulated one-slot second-price auction against 115 other campaigns, so diminishing returns are
  the market's, not an assumed curve. The split is a cheapest-purchase-first fill (partial last bucket) that is provably the
  optimum of the underlying linear program (checked against an LP solver in tests), with a forecast range and a reason per bid.
- **Config shape:** what billing, serving and an approver each need: pricing, per-host budget and bids, bid phases, creatives,
  guardrails, measurement, open questions (rationale: D2, D6).
- **Serving** (code + tests, not on the page): host + zip + first name → features with confidences → persona posterior → eCPM
  auction, in milliseconds, no LLM. **Speed:** the plan shows after intake (~13 s); creatives stream in after (~45 s).

Prompts: `prompts/`. Architecture, engineering decisions and the production path: `docs/ARCHITECTURE.md`. Every decision and
rejected alternative: `docs/DECISIONS.md` (D1–D36). The score with a worked example: `docs/SCORING.md` §0.

## How I know it works (`docs/EXPERIMENTS.md`, `reports/`)
Hosts: labels for the 15 briefs written blind; 100 unseen brands split 49 dev / 51 test (test touched once per decision); an LLM
judge on host pairs; sensitivity on every constant. Money: an LP-solver check; replays with fresh rival bids. Copy: a separate critic
and code rule checks. A change ships only if it holds on these; each decision lists the alternatives that lost (an additive score,
dropping the persona branch), and a fit that had trained on the test set was withdrawn.

| hosts (15 briefs, labels written blind) | | creatives (fresh run, 44 personas) | | money (simulated market) | |
|---|---|---|---|---|---|
| precision of recommended hosts | **0.91** | personas with a passing line | 43% → **73%** | split vs LP optimum | **exact** |
| must-include recall@5 | 0.77 | critic: claims OK | 88% → **94%** | forecast range covers replay | 79% |
| must-exclude violations | 1 | prompt-injection cases | 4/4 | budget spent | **100%** |
| 49 unseen brands: LLM judge agrees | 89.7% | paraphrase stability (top-5) | 0.71 → 0.84 | clearing CPC in benchmark | 20/20 |

**Limits:** 15 golden briefs is small; the judge favours same-category hosts; the critic is the writer's model family; the market
is simulated; CTR/CVR are benchmark priors.

## Hard vs easy, and where the interesting work is
**Easy:** calling an LLM, schemas, the UI. **Hard:** (1) *matching with no feedback*: the ranking is a cold-start prior, so the
work is making it honest (exclusions with reasons, weak-fit flags, competitors)
and measurable without clicks (above). (2) *LLM variance and grounding*: one rewording swapped a third of the top-5 until
self-consistency; copy stays honest only if writer and critic see the same facts. (3) *money with no market data*: prices from
competition, an exact optimiser, and ranges instead of point forecasts. (4) *weak serve-time signals that compound*: billing vs
shipping zip changes who you target, so features carry confidence and never trigger hard rules.

## Next week (one more week, still no logs: make what exists more trustworthy)
1. **Check the evals against people:** label the 40 pending pairs + ~100 more; measure how often the LLM judge agrees.
2. **A critic from another model family**, checked against human ratings on ~50 lines; creatives only after a vague brief is clarified.
3. **Intake recall** (0.77; the misses are known) and the pending intake-prompt A/B.
4. **A better optimiser:** a minimum learnable spend per host (mixed-integer: $0 or enough to learn, no $35 slivers) and a
   risk-aware split (purchases − λ·variance from the replays, a quadratic program) instead of the hand-set share cap; forecast
   range from the same replays (79% → 80%+).
5. **Ready for scale:** CI with the tests and cached golden eval as gates; a synthetic 1,000-host catalog for retrieval and latency.

With logs (production, `docs/ARCHITECTURE.md` §4): learned pCTR/pCVR with this scorer as the prior, a bandit, a pacing service.

## Cut, on purpose
Learned models and real pacing (no outcome data to fit them honestly) · LLM re-ranking of hosts (variance and cost; the
deterministic scorer won on evals) · image creatives · auth and multi-tenant storage · the serving simulator and eval pages (the
demo is the planner; serving and evals live in code, tests and `reports/`).
