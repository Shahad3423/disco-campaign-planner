# Architecture: prototype now, production later

## Two stages, two very different budgets

```
                ┌──────────────── STAGE 1: CAMPAIGN PLANNING (per advertiser) ────────────────┐
 advertiser ──► │ intake/parse (LLM) ─► gate ─► publisher scoring (code) ─► persona selection │
   text         │      │ low confidence → clarifying questions                                │
                │      ▼                                                                       │
                │ creative gen (LLM) ─► critic (LLM) ─► budget + bids (code) ─► CampaignConfig │
                └───────────────────────────────────────────────────────────┬─────────────────┘
                                                                            │ approved config + creatives
                ┌──────────────── STAGE 2: IMPRESSION SERVING (per shopper) ▼─────────────────┐
 {publisher_id, │ features (name, zip, platform) ─► persona posterior ─► eligible campaigns    │
  zip,          │ (allow/block lists, budget pacing) ─► rank by eCPM × relevance ─► pick       │
  first_name} ─►│ creative variant ─► render "{first_name}" safely ─► log impression          │
                └──────────────────────────────────────────────────────────────────────────────┘
```

| | Planning | Serving |
|---|---|---|
| Volume | thousands of advertisers | millions of impressions/day |
| Latency | seconds are fine | tens of milliseconds |
| LLM | yes | **no**: creatives are pre-generated templates |
| State | campaign store | feature lookups + budget counters |

## Prototype (this repo)

```
src/disco_planner/
  domain/      Pydantic models: AdvertiserProfile, PublisherScore, Persona, Creative, CampaignConfig
  data/        catalog loaders (publishers, personas) + reference tables (names, zips)
  llm/         LLMProvider interface; gemini / anthropic / openai / fake adapters; prompt loader
  planning/    intake, publisher_scoring, personas, creative, budget, pipeline
  serving/     features, persona_inference, auction, render
  eval/        golden labels for the 15 example advertisers + runner
config/        scoring weights, pricing priors, budget rules (YAML)
prompts/       every prompt the system uses (loaded at runtime, single source of truth)
app/           Streamlit UI
tests/
```

## Production path (what changes at Disco's scale)

1. **Scoring → learned ranking.** Today's hand-weighted scorer is a cold-start prior. With
   impression/click/conversion logs, train pCTR/pCVR models (gradient-boosted trees first, then a
   two-tower retrieval model once there are thousands of brands × hosts). The LLM's profile becomes
   input features for brand-new advertisers that have no history.
2. **Creative selection → contextual bandit** (Thompson sampling over variants per persona/host),
   so the 3–5 variants are an experiment, not a guess.
3. **Features → feature store.** Name and zip tables are static lookups (sub-millisecond, cached in
   memory). Publisher aggregates are refreshed in batch. Budget counters live in a low-latency store
   (e.g. Redis) and are updated by a streaming pacer.
4. **Serving → stateless service** behind the checkout extension: horizontally scalable, with the
   persona posterior and eligibility filters computed per request, plus a fallback creative if any
   dependency is slow.
5. **Planning → async jobs** with cached LLM results keyed on input hash, prompt versioning, and
   offline evals in CI before a prompt change ships.
6. **Measurement.** Holdout groups per advertiser for incremental lift (not just attributed
   conversions), calibration dashboards per feature segment, and alerts on zip-source drift.
