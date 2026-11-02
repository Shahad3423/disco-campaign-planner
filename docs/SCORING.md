# Scoring: what the numbers are, which ones matter, and how they become learned

## 0. In plain language: how a host gets its score

For every host we ask three questions, and multiply the answers:

1. **Will this host's shoppers care about the product?** (`category_fit`, 0 to 1)
2. **Can they afford it?** (`price_fit`, 0 to 1, entering as its square root)
3. **Is everything else about the match good?** (`quality`, a weighted average of up to five parts)

Multiplying is deliberate: a host that is irrelevant or unaffordable can't be rescued by a good audience match.

**The five quality parts** (weights in `config/scoring.yaml`, hand-set):

| Part | Weight | In one line |
|---|---|---|
| `persona_fit` | 0.35 | Would the shopper types who buy this product be shopping at this host? |
| `audience_fit` | 0.30 | Gender and age overlap between the advertiser's stated buyer and the host's shoppers. |
| `notes_fit` | 0.20 | Did the LLM find the host's free-text notes supportive, neutral or in conflict? |
| `seasonality` | 0.10 | Gifting products only: is it gifting season and a gifting host? |
| `scale` | 0.05 | Log of monthly impressions. Tiny, so big hosts don't win on size alone. |

A part we have no information on (no stated audience, not a gifting product) is **left out and the remaining
weights are rescaled**, rather than filled with a neutral number that would pretend to know something (D25).

### Why `persona_fit` takes three steps

The data has no direct link between an advertiser and a host. What it does have is **personas** (who buys what)
and each **host's demographics**. So the chain goes advertiser -> plausible personas -> how present those
personas are on this host. This is the "two-hop" design. The persona weights are computed anyway to choose which
personas to write creatives for, so reusing them here costs almost nothing.

```
persona_fit(host) = sum over personas of  weight(persona) x host_fit(persona, host)  /  sum of weights

weight(persona)  = affinity x price_compat x audience_compat      "how likely is this persona to buy THIS advertiser?"
  affinity         0.6 for one shared interest tag, +0.2 per extra tag (cap 1); 0 if none
  price_compat     lookup: advertiser price tier x persona price sensitivity (table in scoring.yaml)
  audience_compat  0.5 x gender match + 0.5 x age overlap (1.0 if the advertiser stated neither)
host_fit(persona, host) = mean(age overlap, gender term, category term)   "how present is this persona on THIS host?"
```

**Worked example** (a premium dog-walking-gear brand targeting women 25-45, scored on Pawline, whose shoppers are
30-55 and 62% female). Two personas are plausible:

| | weight = affinity x price x audience | host_fit = mean(age, gender, category) |
|---|---|---|
| Pet Parent | 0.60 x 0.90 x 0.825 = **0.446** | (0.91 + 0.88 + 1.0) / 3 = **0.930** |
| Affluent Classic | 0.60 x 1.00 x 0.65 = **0.390** | (0.28 + 0.62 + 0.4) / 3 = **0.433** |

`persona_fit = (0.446 x 0.930 + 0.390 x 0.433) / (0.446 + 0.390) = 0.698`. The Pet Parent fits the host well and
counts for more; the Affluent Classic (50-68) is a weak fit and pulls the average down from 0.93.
With all five parts known, Pawline's quality is 0.30 x 0.685 + 0.35 x 0.698 + 0.20 x 1.0 + 0.05 x 0.158 +
0.10 x 0.3 = 0.688, and with `category_fit` = `price_fit` = 1.0 its score is 0.688.

**Known weaknesses, stated plainly.** The numbers inside this chain (0.6 and 0.2 for tags, 0.8 and 0.3 for
gender, 0.4 for a category miss, the whole `price_compat` table) are hand-set, not fitted. Price and audience
enter twice: once inside the persona weights and again as `price_fit` and `audience_fit`. We tested what that
costs (EXPERIMENTS.md, "Is the persona branch worth its length?"): removing `persona_fit` hurts, and removing the
double counting changes nothing measurable.

## 1. The score

```
score(advertiser, host) = category_fit^a  x  price_fit^b  x  M_p(audience, persona, notes, scale, seasonality; w)
                          (will they care?)   (can they buy?)  (everything else, power mean with weights w)
```
Today a = 1, b = 0.5, p = 1 (weighted average). Components are in [0, 1] and each carries a one-line reason.
Hosts are then recommended by cutoffs (min category fit, absolute floor, fraction of the top score, max 6;
max 3 if no host is a strong fit). Competitor status is **not** in the score (D21): it's a flag, and blocking
is a host setting enforced at serving.

**Why multiply.** Take logs: `log score = a·log category + b·log price + log M_p(...)`. The score is a
**log-linear model** with hand-set coefficients, the same functional form as a logistic/Poisson model with
a log link, and the same algebra as a funnel (P(click) x P(buy | click)) or naive Bayes (prior x likelihood
ratios). Relevance and affordability are gates: a perfect audience can't rescue an irrelevant host. The
first additive version failed exactly there (meal kits scored like pet hosts for dog food; EXPERIMENTS.md).

## 2. Which constants matter (global sensitivity)

40 constants, Morris screening (Morris 1991; Saltelli et al. 2008): 20 random trajectories, each constant
over [x0.5, x1.5] (exponents and p over fixed ranges), 820 re-rankings of the 15 golden briefs, no LLM calls.
Full table: `reports/sensitivity.md` (`python -m disco_planner.eval.sensitivity`).

| tier | constants | mu* (top-5 change) |
|---|---|---|
| decisive | `price_exponent` (sigma 0.23: strongly interactive), `quality_p`, `shape.neutral` (value used when the brief didn't say) | 0.12-0.17 |
| moderate | `relevance_exponent`, cutoffs (`relative_to_top`, `min_score`, `min_category_fit`), `category_only_cap` | 0.06-0.08 |
| small | income-gap penalty, price spreads, attribute caps, persona weight | 0.02-0.05 |
| negligible | the other quality weights, persona shape constants, seasonality values, notes penalty: 12 of 40 have no measurable effect | < 0.02 |

Reading: **structure beats weights.** The exponents, the compensation parameter p, missing-data handling and
the cutoffs decide rankings; the quality weights barely do. This is the classic "robust beauty of improper
linear models" (Dawes 1979) / flat-maximum effect: with positively correlated signals, equal or arbitrary
positive weights rank almost as well as optimal ones. Practical consequence: tune the 6-8 decisive
constants with data; leave the rest at round defaults and say so.

Two findings worth naming: `price_exponent` is the precision/recall lever (raising it costs ~0.2 recall), and
`shape.neutral` is a **missing-data** decision (most briefs omit age, gender or price). In production that
becomes missingness indicator features the model learns, not one constant.

## 3. Making them better now (no outcome data yet)

| method | what it does | status |
|---|---|---|
| pairwise labels + Bradley-Terry fit | LLM judge picks the better host for an advertiser, asked in both orders (position-bias filter); a pairwise logistic model fits a, b, p, w with a prior centred on today's values (MAP) | done: `eval/pairwise_labels.py`, `eval/fit_scoring.py`, results `reports/fit_scoring.md` |
| human spot check | 40 judge pairs in `reports/human_labels_todo.csv` for a person to answer; agreement tells how far to trust the judge | waiting for a human |
| expert elicitation (AHP, Saaty 1980) | an account manager's pairwise "X matters 3x more than Y" -> consistent weights | option |
| Bayesian optimisation (Snoek et al. 2012) | tune cutoffs on a bigger labelled set with cross-validation | option, once there are more labels |
| anchor to meaning | make constants estimates of something real (e.g. a host's approval rate from its allow/block history) | done for competitor blocking (now a host setting) |

Protocol: train on holdout-brand pairs, cross-validate **by advertiser**, test on the 15 golden examples
(labelled by a different agent, never used for fitting). Caveat: both label sources are LLMs, so this measures
agreement with a careful judge, not true performance; that only comes from outcomes.

## 4. In production: learn what we actually want

Predict the auction quantity directly: **expected value per impression = P(click) x P(purchase | click) x value**.
The components above become features.
1. **Log-linear / logistic regression** on log-features: learns the exponents (a, b, ...) directly; calibrated;
   the standard first model for ad CTR (McMahan et al. 2013, "Ad Click Prediction: a View from the Trenches").
2. **Gradient-boosted trees with monotone constraints** ("more category fit never lowers the score"): interactions
   with sane explanations (He et al. 2014, "Practical Lessons from Predicting Clicks on Ads at Facebook").
3. **Explainable Boosting Machines / GA2M** (Lou, Caruana et al. 2013; InterpretML, Nori et al. 2019): a learned
   curve per component instead of our hand-picked shapes (the 1.5 / 2.5 price spreads), still plottable.
4. At thousands of hosts: two-tower retrieval + a cross-feature ranker (Wide & Deep, Cheng et al. 2016;
   Deep & Cross, Wang et al. 2017; factorization machines for sparse crosses, Rendle 2010).

What makes it hard: logged data is biased by what we chose to serve (exploration slice with Thompson sampling,
Chapelle & Li 2011; counterfactual / off-policy evaluation, Bottou et al. 2013); the auction multiplies bid by
P(click), so probabilities must be **calibrated** (Niculescu-Mizil & Caruana 2005); new advertiser x host pairs
have no data, so estimates shrink toward the model's prediction (empirical Bayes), and today's hand score is
that prior. The functional form stays the same from prototype to production; only where the coefficients
come from changes: hand-set -> fitted on labels -> learned from logs.

## 5. Better composites: the options

| aggregator | formula | can a strong signal cover a weak one? | use when |
|---|---|---|---|
| weighted arithmetic mean | sum w x | fully | independent, interchangeable signals |
| weighted geometric mean (our outer form) | prod x^w | partly; a zero kills it | funnel steps that must all happen |
| **weighted power mean** (implemented: `quality_p`) | (sum w x^p)^(1/p) | tunable: p=1 average, p->0 geometric, p->-inf minimum | one learnable knob for "how compensatory" |
| ordered weighted average (Yager 1988) | weights by rank of the values | tunable and-ness / or-ness | "at least 2 of 3 must be strong" |
| Choquet integral (Grabisch 1996) | weights on sets of criteria | models interactions | "price matters only when relevance is high" |
| TOPSIS / outranking (PROMETHEE, ELECTRE) | distance to ideal / pairwise dominance | limited | shortlisting with hard trade-offs; not for serving |
| naive Bayes / likelihood ratios | prior x prod LR_i | multiplicative with a probability meaning | the cleanest "why multiply" |

Recommendation: keep the multiplicative outer structure (it's the funnel), let the data choose p for the
inner aggregation (now a config value), and move components toward calibrated estimates so the score becomes
an expected value, not just an ordering.

## 6. Where personas fit

Personas appear three times:
1. **Creative**: one ad variant per selected persona (the brief requires it).
2. **Inside the publisher score**: `persona_fit`, the two-hop term = sum over plausible personas of
   (persona plausibility for this advertiser) x (how present that persona is on this host). It catches what
   tags miss, e.g. an age-agnostic activewear brief vs a 50-70 host.
3. **Serving**: the shopper-level persona posterior picks which variant to show and sets the bid's
   conversion-likelihood multiplier.

**Sparsity.** In the prototype the persona x host table is not sparse: `host_fit` is computed from attributes
(age overlap, gender share, category affinity), so every pair has a value. In production the *observed*
persona x host x advertiser outcomes would be very sparse, and worse, personas are never observed: a shopper
isn't labelled "Busy Parent". So personas can't be a learned cross-feature directly. The production version
treats them as a **latent** layer: learn shopper, host and advertiser embeddings (factorization machines /
two-tower share factors across sparse crosses), and keep personas as (a) a creative-writing device and
(b) human-readable labels attached to regions of that embedding space. What is directly observable and
learnable is variant x host performance (the variant id is logged), which a bandit with a shared
(hierarchical) prior handles.

## References
Bottou et al. (2013) Counterfactual reasoning and learning systems, JMLR · Burges (2010) From RankNet to
LambdaRank to LambdaMART · Chapelle & Li (2011) An empirical evaluation of Thompson sampling, NeurIPS ·
Cheng et al. (2016) Wide & Deep learning for recommender systems · Dawes (1979) The robust beauty of improper
linear models, American Psychologist · Grabisch (1996) The application of fuzzy integrals in multicriteria
decision making, EJOR · He et al. (2014) Practical lessons from predicting clicks on ads at Facebook, ADKDD ·
Lou, Caruana, Gehrke, Hooker (2013) Accurate intelligible models with pairwise interactions, KDD · McMahan et
al. (2013) Ad click prediction: a view from the trenches, KDD · Morris (1991) Factorial sampling plans for
preliminary computational experiments, Technometrics · Niculescu-Mizil & Caruana (2005) Predicting good
probabilities with supervised learning, ICML · Nori et al. (2019) InterpretML · Rendle (2010) Factorization
machines, ICDM · Saaty (1980) The Analytic Hierarchy Process · Saltelli et al. (2008) Global Sensitivity
Analysis: The Primer · Snoek, Larochelle, Adams (2012) Practical Bayesian optimization of ML algorithms,
NeurIPS · Thomas et al. (2024) Large language models can accurately predict searcher preferences, SIGIR ·
Wang et al. (2017) Deep & Cross network for ad click predictions, ADKDD · Yager (1988) On ordered weighted
averaging aggregation operators in multicriteria decision making, IEEE SMC.
