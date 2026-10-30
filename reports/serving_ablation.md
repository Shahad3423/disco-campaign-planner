# Serving ablation: which shopper signals change decisions?

20 hosts x 15 names x 10 zips x 2 timestamps; 4800 requests had at least one eligible campaign.

| signal removed | decisions changed (top campaign or variant) |
|---|---|
| first name (gender/age prior) | 31.9% |
| zip (geo + local time) | 0.0% |
| timestamp (Nov 20 vs Jan 5) | 4.2% |

The platform (host) is fixed in every row: it is the dominant signal by construction, since
it decides which campaigns are eligible at all. A weak signal that changes many decisions is a
warning sign (flip-flopping between near-tied personas), not a sign of value: see the
switch_margin setting in config/serving.yaml and docs/EXPERIMENTS.md.
