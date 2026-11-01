# Decision Log

Every non-obvious choice, why we made it, and what would change our mind.
Format: **Decision → Why → Alternatives rejected → Revisit when.**

---

## D1. Disco's business model frames the whole design

Disco runs a post-purchase ad network: right after a shopper checks out at Brand A, Brand A's
thank-you / order-status page shows "a small set of curated offers from non-competing brands".
Advertisers "set a bid, pay per click" and are billed monthly. Host brands (publishers) get "a
share of the revenue" and "pick the brands" they allow (source: Disco's Shopify App Store listing;
disco.co is now an unrelated company).

Consequences for this prototype:
- **Publishers are other brands' checkout pages.** A publisher that sells what the advertiser sells
  is a *competitor* and will likely block the ad. Example: linen bedding (#14) on Northbed, dog food
  (#1) on Pawline. We flag a `competitor_conflict` and down-rank it instead of ranking it #1.
- **The shopper has just completed a purchase.** The moment matters: they are in buying mode, and
  the publisher's AOV is a strong signal of what they just spent.
- **The publisher's page is their brand.** Bad or irrelevant ads hurt the host, so relevance floors
  and frequency limits protect the supply side, not just the advertiser.

## D2. Three parties, several pricing models, one ranking currency

**Decision.** Support **CPC (default), CPM, and target-CPA (tCPA)**, plus an optional
**advertiser-funded cashback offer**. Internally, every bid is converted to **expected revenue per
1,000 impressions (eCPM)** so mixed pricing models can compete for the same slot:

| Pricing | eCPM formula | Who carries the risk |
|---|---|---|
| CPM | `bid_cpm` | Advertiser (pays for views) |
| CPC | `bid_cpc × pCTR × 1000` | Shared: advertiser pays for clicks, Disco for CTR |
| tCPA | `target_cpa × pCTR × pCVR × 1000` | Disco (pays for conversion-prediction error) |

**Which model we recommend to an advertiser:**
- **CPC by default.** It is Disco's documented model, it fits new advertisers with no conversion
  history, and clicks are observable without trusting the advertiser's pixel.
- **CPM, capped, only for awareness goals** (launches, gifting-season pushes) where the advertiser
  explicitly values reach.
- **tCPA only after a learning phase**: conversion tracking verified and enough conversions to
  predict CVR. Offering it on day one would make Disco eat prediction error on a cold-start brand.
  Attribution is already a trust issue in Disco's public reviews (complaints about 14-day
  view-through conversions), so tCPA is billed on **click-through conversions only**, with
  view-through reported separately and never billed.

**The parties stay aligned:**
- **Advertiser** pays only within the CPA they can afford (see D6).
- **Publisher** earns `rev_share × spend` on their page and keeps allow/block control over brands
  and categories. `rev_share` is a config value; we assume 0.5 and label it as an assumption.
- **Disco** keeps `1 − rev_share`, and ranks by eCPM × relevance, so it doesn't sell a slot to a
  high bidder whose ad is irrelevant enough to hurt the host's page.
- **Shopper** sees at most `max_offers_per_page` offers, with frequency caps.

**Revisit when** real take rates, rev-share terms, or minimum bids are known.

## D3. Data available about a shopper: platform, zip code, first name. Nothing else.

Per the case study, at serve time we receive only: **the platform (publisher) the shopper is on,
their zip code, and their first name.** We do not know what they bought, their device, or
their history. Nothing in the assignment folder adds shopper-level fields (we checked).

So the system has **two stages with different inputs**:
1. **Campaign planning** (once per advertiser, seconds, LLM allowed): advertiser text + catalog →
   publishers, personas, creatives, config. This is the assignment's core deliverable.
2. **Impression serving** (millions per day, milliseconds, no LLM): `{publisher_id, zip,
   first_name}` → inferred features → persona posterior → choose campaign + creative → render
   with the first name. The prototype includes a simulator for this stage, because it is what
   Disco's production ML actually does and it shows how planning outputs get used.

## D4. Feature engineering from the three signals, with explicit confidence

| Order | Feature | Source | Confidence | Used for |
|---|---|---|---|---|
| 1st | Publisher category, AOV, audience demographics | platform → catalog | High (catalog fact) | Cart prior, demographic prior |
| 1st | Gender probability | first name → SSA name statistics | Medium (unisex names, nicknames) | Persona posterior |
| 1st | Age distribution | first name → SSA births by year | Low–medium (wide spread) | Persona posterior |
| 1st | State, region, timezone | zip → Census ZCTA / zip3 tables | High *if the zip is the shopper's* | Geo match, local time |
| 2nd | Likely cart (category, price band) | publisher subcategories + AOV | Medium | Complementary-category affinity |
| 2nd | Local time of impression | timezone × server time | Medium | Late-night/impulse relevance (e.g. Swiftcart) |
| 2nd | Area income tier, urbanicity | zip → ACS median income, density | Medium (area ≠ person) | Price-point fit |
| 2nd | Climate / terrain (e.g. snow, mountain) | zip → state/lat | Medium | Category relevance (ski shells) |
| 3rd | Affordability gap | area income × advertiser price vs publisher AOV | Low | Soft down-weighting only |
| 3rd | Gift-purchase probability | zip mismatch, Nov–Dec season, giftable publisher | Low | Gifter-mode persona |
| 3rd | Persona posterior | publisher prior × name likelihood × zip likelihood | Composite | Creative selection |

**Not used, on purpose: inferred ethnicity from names.** It's technically possible but it is a
sensitive inference under US state privacy law (CPRA treats racial/ethnic origin as sensitive
personal information), it creates discrimination risk, and it adds nothing we need for choosing
between candles and dog food. It's an easy question to defend in a review.

## D5. The zip-code problem and compounding error

The zip we receive may be the **billing** zip or the **shipping** zip, and they differ for gifts,
work deliveries, and people who move. The person looking at the thank-you page is the **buyer**,
so buyer-centric features (income area, local time, climate) should come from the **billing** zip.
Using the shipping zip targets the gift *recipient's* area instead, which is a silent bias
(and apparently a known issue in Disco's systems).

Guardrails against error compounding through 2nd/3rd-order features:
1. **Distributions, not labels.** Name → `P(female)=0.93`, not `female`. Age is a distribution.
2. **Bayesian combination with a strong prior.** The publisher's audience is the prior; name and
   zip only *update* it. A weak or wrong signal moves the posterior a little, not completely.
3. **Inferred features never apply hard rules.** Exclusions come only from facts: the advertiser's
   own statements, publisher category, competitor conflicts. Inferred features only re-weight.
4. **Confidence gating.** If the persona posterior is too flat (max probability < τ), serve the
   publisher-level default creative instead of guessing a persona.
5. **Zip provenance is a field.** `zip_source ∈ {billing, shipping, unknown}`. Unknown → geo
   features get lower weight. A billing/shipping mismatch becomes a *gift* signal instead of noise.
6. **Measure it.** In production: log feature values and outcomes, check calibration by segment,
   and run an ablation (with/without each feature) before trusting it.

## D6. Budget: set it the way a careful advertiser would

An advertiser doesn't start from "I have $10k." They start from "what can I afford to pay per
customer?" Steps:
1. **Max affordable CAC** = `price × gross_margin × expected_orders` (expected orders > 1 for
   subscriptions). We ask for margin and price; if missing, we use category defaults *and say so*.
2. **Target CPA** = `max_CAC × safety_factor` (default 0.7, so there's room for profit and error).
3. **Max CPC bid** = `target_CPA × assumed CVR`, where CVR is a prior per publisher, scaled by fit.
   The CPC range shown is low/mid/high CVR assumptions, not a single fake-precise number.
4. **Learning budget** = enough clicks per publisher to make a decision (default ≥ 300 clicks for a
   usable CTR/CPC read; conversions are reported as directional at that volume). We tell the
   advertiser which decisions that budget can and can't support.
5. **If the budget is too small for all recommended publishers, concentrate.** Fewer publishers
   with real signal beat many publishers with noise.
6. **Allocation** = fit-weighted, capped by each publisher's reachable inventory, with a minimum
   per publisher and an exploration reserve (default 15%) for lower-ranked candidates.
7. **Pacing and stop-loss:** daily cap = budget/days with even pacing (front-loaded for gifting
   brands in Nov–Dec); pause a publisher if CPA > 2× target after it has had enough clicks.

## D7. Uncertain advertiser input → ask, don't guess

The parser returns a **confidence** and the **missing fields** that matter (product, price point,
audience, goal). Below a threshold, the UI asks 2–4 specific questions ("What does it cost?",
"Who buys it?") and shows a clearly labeled *exploratory* draft, or none at all for meaningless
input ("idk just try it"). Advertisers outside the network's model (B2B SaaS for dentists) get an
honest "Disco's consumer checkout audience isn't a fit" with zero publishers. It's better to lose a
sale than to burn an advertiser's budget.

## D8. LLM handles language; code handles numbers and ranking

The LLM extracts the advertiser profile, writes explanations and creatives, and critiques the
creatives. **Ranking, scores, budgets, and bids are deterministic Python** with weights in config.
Why: auditable (every score has a breakdown), reproducible, testable, cheap, no invented
publishers or numbers, and easy to change live. The LLM may *explain* a ranking but cannot change it.

## D9. LLM provider: Gemini Flash (free tier) behind a provider interface

The app needs an API key; a Claude Pro subscription does not include API credits (Pro usage
limits only apply to building with Claude Code). Google AI Studio's free tier for Gemini Flash
models is enough for this workload (≈4–6 calls per campaign). The code talks to a small
`LLMProvider` interface with Gemini, Anthropic, and OpenAI adapters plus a `Fake` provider for
tests, so switching is a config change. Mock data only, so free-tier data-use terms are acceptable.
**Committed cached runs** for all 15 example advertisers keep the hosted demo working if the
free quota runs out mid-review.

## D10. Hosting: Streamlit Community Cloud

- Free, deploys from GitHub on every merge to `main`, has a secrets manager for the API key, and
  runs Python natively, so the stack is one language that the author can defend line by line.
- **Vercel** rejected: built for JS/serverless; Streamlit needs a long-running server.
- **Render/Railway free tiers** rejected: cold starts of up to a minute after idling, which is a
  bad first impression for a reviewer.
- **Risk:** Streamlit apps sleep after days of inactivity. Mitigation: open it before submitting
  and before the interview.

## D11. Modular package, config-driven

Scoring weights, pricing priors, and budget rules live in `config/*.yaml`. Every stage is a pure
function `input model → output model` (Pydantic) so stages can be tested, swapped, or moved to a
separate service later without changing callers.

## D12. Settlement type from USDA RUCA, not Census ACS

**Decision.** Urban core / suburban / small city / small town / rural comes from the USDA ERS 2020
Rural-Urban Commuting Area codes per ZIP (public, no key). RUCA 1 (metro core) is split into urban core
vs suburban with the zip's land-area class. **Why:** RUCA is the standard commuting-based definition and
needs no API key; the Census ACS path (income, density) was dropped when no key was available, so
`income_tier` stays at confidence 0 by design rather than being faked. **Revisit when** ACS zip tables
are available: density and income would sharpen the urban/suburban split and affordability.

## D13. Household / life-stage features (3rd order) and how they're kept honest

**Decision.** From age-from-name x settlement type (x host notes) we derive P(has kids), P(homeowner), a
life-stage distribution, car dependence, outdoor lifestyle and "season now" needs, using approximate
national rates in `config/household.yaml`. Example: a 35-44-year-old in a suburb is more likely to have
kids, so family products get a (tempered) boost. **Guards:** the name-based age (confidence <= 0.15) is
first shrunk toward the national adult age mix by its confidence, so a weak name can't fake a life stage
(tested); derived confidence = settlement confidence x (0.4 + 0.6 x age confidence); every feature keeps
its inference chain in `source`; usage is only through tempered persona likelihoods and CTR boosts, never
hard rules. **Measured:** with the 15 example campaigns (none sells kids/family products) household
features change 0.4% of serving decisions; see EXPERIMENTS for the test with family-product campaigns.

## D14. Holdout run on a cheaper model

The free-tier daily quota for gemini-3.5-flash ran out mid-holdout. Rather than mix models inside one
eval, the 100-brand holdout was re-run entirely on gemini-flash-lite and is labelled as such. It tests
whether the design (closed vocabularies, code-side scoring and policy) holds with a weaker model; it is
not the production model's score.

## D15. Bid strategy: phased, with a hard break-even ceiling

**Decision.** The config carries a `bid_strategy` and serving computes a final bid per impression:
`final = clamp(start x conversion_likelihood x pacing, floor, max)`.
- *learning* (first 14 days or 300 clicks/host): fixed start bid = 70% of break-even, pacing only.
- *enhanced_cpc*: conversion_likelihood = this shopper's persona match for the campaign / the host's
  average shopper's (0.8-1.25x): the glossary's "bid higher when the user looks like a high-converter".
- *target_cpa*: Disco bids to the advertiser's CPA, only after 50 click-through conversions.
- Reach/impression goals use a fixed CPM with pacing only.
**Why phased:** adjusting bids before CTR/CVR are measured confounds the measurement. **Why the ceiling:**
max CPC = target CPA x CVR, so no click can cost more than it's expected to earn.

## D16. What a customer is worth: target CPA > target ROAS > margin

Most marketers know a target CPA or ROAS, not a margin; the form accepts all three, first one wins.
Margin (with a labelled category default) is only the last resort, because without *something* the
bid ceiling would be arbitrary.

## D17. Delivery goals: budget, impressions or reach

Advertisers can set a budget, or an impressions / unique-shopper target that we convert into a budget
(reach x assumed frequency 1.6 -> impressions -> cost per 1,000 at each host). The plan reports expected
impressions and reach either way, and flags goals the chosen hosts can't deliver in the flight.

## D18. Every prior carries a validation status

`config/pricing.yaml` and `config/household.yaml` tag each number [sourced] (public data: Census HVS
homeownership by age, CPS families with children), [benchmark] (industry ranges: e-commerce conversion
~2.5-3%, category spreads) or [assumed] (no public data: third-party thank-you-page CTR, approval
probability, share of voice). CVR was lowered to 1/2/3.5% after checking benchmarks; P(kids | 35-44) to
0.52. All are placeholders for rates learned from the network's own logs.

## D19. The page auction: second price, slot effects, reserve

**Decision.** One thank-you page has **one** offer slot (product decision: one clear offer protects the host's page; `slots` is configurable). Eligible campaigns are ranked by eCPM (final bid x expected
CTR x 1000); anything below a reserve eCPM is dropped (an empty slot beats a weak offer on the host's own
page); a position curve applies if slots > 1; the winner pays the
**generalised second price**: the least that still beats the runner-up, `runner-up eCPM / (own CTR x 1000) + $0.01`, never
above its own bid. The host earns its revenue share of what's actually paid. **Why second price:** it makes
"bid what a click is worth to you" the sensible strategy, which is what our bid ceiling (break-even CPC)
assumes. **Why it matters here:** with only the 15 example campaigns no host had more than 3 bidders, so the
ranking only set order; the 100 holdout brands are loaded as competitors so slots are actually contested.

## D20. Auction insights instead of visible rival bids

Bids are sealed: a campaign states a ceiling once (derived from its target CPA) and Disco's server runs the
auction per page view, so there is no bid-and-counter-bid between advertisers (that dynamic belongs to
first-price auctions). With one slot, second price is exactly truthful: bidding your break-even is the best
strategy, and what you pay depends only on the runner-up. What advertisers get instead is aggregate feedback
per host (win rate, average price paid, which campaigns they lose to, and the bid that would win 25/50/75%
of auctions compared with their break-even), so the decision is "raise my target CPA or leave this host".
Rival names appear (like Google's Auction Insights), rival bids never do (tested). Here the aggregate is
simulated over sample shoppers; in production it comes from the auction logs.

## D21. Competitor blocking: a host setting, not a scoring penalty (supersedes the D1/D2 penalty)

**Measured:** only ~6% of all advertiser-host pairs are competitor-flagged (15 examples: 5.4%; holdout:
6.1%). It looked dominant because in a 20-host catalog the best-fitting hosts are the ones selling similar
things (26-38% of *recommended* pairs). In a network of thousands of hosts that share shrinks.
**Decision.** Fit is fit: no competitor penalty in the score and no budget discount. A host that also sells
the advertiser's product type gets a flag ("may need to opt in"); whether it actually blocks is the host's
own setting, enforced at serving (`host_policy`, simulator toggle), and blocked budget simply reflows to the
other hosts. Flags need 2 of 3 intake samples to agree. **One guard kept:** if *every* pick is flagged, the
best non-competing host with a real category link is added as a fallback so the campaign can always run
(dog food: Pawline and Ruffco lead on fit, Tailcrate is the fallback).
**Earlier version** (score x P(approve)=0.4, union voting, capacity discount) over-weighted a rare event
three times; kept in git history and EXPERIMENTS for the comparison.

## D22. Cutoff anchored to the best non-competing host; broad-category hosts need audience evidence

The relative cutoff ("at least half the best host's score") was anchored to the top host. Once competitors
ranked on true fit, a competitor (which may refuse) often set that bar, pushing good non-competing hosts out;
a fallback rule patched it. **Decision:** anchor to the best non-competing host with a real category link (it
always clears its own bar, so the fallback rule is gone); competitors are still listed if they clear it.
Anchoring lower let a weak tail back in (sustainable activewear: two 45-70 women's apparel hosts linked only by
"apparel"), so a host linked **only** by a broad category also needs persona fit >= 0.5.
**Evidence (holdout TEST brands, judge pairs where exactly one host is recommended):** the judge prefers the
recommended host 71% (old penalty) -> 82% (fit-only) -> 82% (this version), while golden recall returns to 0.70
with 0 must-exclude violations.

## D23. Budget is the advertiser's cap; objective + optional goal; targets only if set; guardrails vs guidance

Supersedes the "delivery goal instead of a budget" and "suggested learning budget" designs (D6, D17).
- **Budget:** always the advertiser's own hard cap; never suggested. Without it we still rank hosts, pick
  personas and write creatives, but allocate nothing and ask for it.
- **Objective:** conversions (default), clicks, impressions or reach; conversions/clicks are bought per click,
  impressions/reach per 1,000 views. "Maximise conversions" means the COUNT within budget, never the rate.
- **Goal (optional):** a target on top of the budget. The plan forecasts what the budget buys and says whether
  the goal is reachable, money-limited (with the budget needed) or inventory-limited (more money won't help;
  extend the flight or add hosts). Default when reachable: stop at the goal and keep the rest.
- **Bidding:** maximise within budget (default), target CPA or target ROAS. A target is an average the bidder
  aims at, not a per-click limit, and exists only if the advertiser sets one.
- **Guardrails (hard):** budget, max CPC cap (advertiser's or a labelled category default), 50% max share per
  host, stop-loss (only with a target), frequency cap. **Guidance (shown, not enforced):** break-even per
  customer from (assumed) margin, and the bid level above which a click loses money.
- **Allocation:** learning floor per host, then water-filling on diminishing-returns curves (scale = host
  inventory). With bids proportional to expected value, every host returns about the same units per dollar,
  so allocation is driven by inventory and saturation; the diversification cap costs ~4% of conversions on the
  dog-food example and removes single-host dependence. With the clicks objective the optimiser buys the
  cheapest available clicks regardless of quality, which is what that objective means.
- Evals and the cached demo plans use a stated $5,000 budget so allocations exist and invariants are checked.

## D24. Prices from a simulated market; Disco optimises the advertiser's objective, not their economics

**What Disco owns vs what the advertiser owns.** Disco is a marketplace paid on spend: it needs advertisers to
hit their goals (renewals), budgets delivered on time (revenue), host pages protected (supply) and trustworthy
billing. The advertiser's margins, break-even and "what a click is worth" are theirs; they express them only via
budget, objective, goal, target CPA/ROAS and max CPC. Removed: category default bid caps, break-even guidance,
margin inputs, stop-loss, learning floors, exploration reserve, the assumed 5% share of voice.
**Market.** Every known campaign (15 demo + 100 holdout) bids a benchmark CPC (by category: e-commerce/Meta/native
ranges) x price tier x relative fit x a fixed per-advertiser spread, varying log-normally per page view (winning
prices in ad auctions are close to log-normal, Cui et al. KDD 2011). Bids are calibrated so the median clearing
price equals a benchmark $0.90/click (benchmarks are prices paid; in a one-slot auction the strongest of many
rivals sets the price, so raw bids would clear ~3x higher). CTR prior raised to 3% (Rokt reports ~4% for
post-purchase offers, vendor claim).
**Bid landscape and diminishing returns.** Per host: the bid needed to win each simulated page view = top rival's
eCPM / our CTR (second price = what we pay). Winning more of a host means outbidding stronger rivals, so each extra
unit costs more: the diminishing return comes from the auction, not an assumed curve.
**Allocation.** Greedy cheapest-units-first across hosts (optimal for this separable problem; tested against brute
force), stopped by budget, goal, aggressiveness (win up to 30/60/90% of a host's page views), the advertiser's max
CPC or target, and an optional per-host share cap. Output per host: bid, win share, average price paid, spend,
units, market price range (the "sensible starting bid range").
**Share cap: off by default.** The bidding eval showed a 50% default cost 24-79% of forecast units when one host is
much better and the others can't absorb the spend; it stays an advertiser setting and the plan reports its cost.
**Evaluation (reports/bidding_eval.md):** clearing CPC median in benchmark range on 20/20 hosts; forecast range
covers 77% of replayed flights (target 80%; next: derive the range from replays to include price uncertainty);
allocation beats equal, score-proportional and all-on-best-host splits; truthful bidding is optimal in all plans.

## D25. Missing information is left out, not filled in; price is penalised only when it exceeds the typical order

**Missing values.** The old neutral 0.6 (used whenever the brief didn't state gender, age, price...) was one of the
most influential constants (Morris mu* 0.12): applied to every host it added a constant inside the quality average,
compressing real differences and moving hosts across the relative cutoff. Now an unstated component is left out
and the remaining weights are renormalised; an unknown price multiplier is 1 (no information); persona price and
audience compatibility are 1 when the tier or audience isn't stated. Kept: notes "no flag" = 0.5, because the LLM
read every note and judged that one not relevant (a reading, not missing data).
**Price.** If the product costs the same as or less than the host's typical order, the shopper can afford it:
price_fit = 1. Only a product costing more is penalised: exp(-ln(price/AOV) / 1.5).
**Measured:** golden metrics unchanged; holdout TEST: judge prefers recommended hosts 84% (was 82%), ranking
agreement 0.790 (was 0.786); the fill constant no longer exists and the price exponent's sensitivity fell 0.17 -> 0.10.
