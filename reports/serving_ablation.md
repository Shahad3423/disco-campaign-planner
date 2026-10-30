# Serving ablation: which shopper signals change decisions?

20 hosts x 15 names x 10 zips x 2 timestamps; 3900 requests had at least one eligible campaign.

| signal removed | decisions changed (top campaign or variant) |
|---|---|
| first name (gender/age prior) | 32.2% |
| zip (geo + local time) | 0.4% |
| household / life-stage features only (P(kids), homeowner, seasons...) | 0.4% |
| timestamp (Nov 20 vs Jan 5) | 4.6% |

The platform (host) is fixed in every row: it is the dominant signal by construction, since
it decides which campaigns are eligible at all. A weak signal that changes many decisions is a
warning sign (flip-flopping between near-tied personas), not a sign of value: see the
switch_margin setting in config/serving.yaml and docs/EXPERIMENTS.md.

| name group | variant or campaign changed | campaign changed |
|---|---|---|
| female-coded | 38% | 0% |
| male-coded | 38% | 0% |
| unisex | 9% | 0% |
