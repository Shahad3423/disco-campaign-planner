# Reports

Current outputs of the evals (`src/disco_planner/eval/`). How each was produced and what changed between runs:
`docs/EXPERIMENTS.md`; why each rule exists: `docs/DECISIONS.md`.

| File | What it is | Made by |
|---|---|---|
| `bidding_eval.md` | prices vs benchmarks, forecast back-test, allocation vs baselines, truthful bidding (D33) | `eval/bidding_eval.py` |
| `holdout.md`, `holdout.json` | 100 real-brand briefs written blind (49 dev / 51 test) | `eval/holdout.py` |
| `holdout_plans/` | the plans for those 100 brands; also the rival campaigns in the simulated market | `eval/holdout.py` |
| `pairwise_labels.jsonl` | LLM-judge labels (both orders) on host pairs, used for the dev-set judge metric | `eval/pairwise_labels.py` |
| `human_labels_todo.csv` | 40 pairs for a human to label, to check the LLM judge | `eval/pairwise_labels.py` |
| `sensitivity.md` | which scoring constants matter (Morris screening on the golden set) | `eval/sensitivity.py` |
| `fit_scoring.md`, `fitted_params.yaml` | a pairwise fit of the scoring exponents (withdrawn: it trained on holdout labels; see EXPERIMENTS) | `eval/fit_scoring.py` |
| `serving_ablation.md` | does each shopper signal change serving decisions? | `eval/serving_ablation.py` |
| `pipeline_report.md` | whole-pipeline before/after table, one row per stage | `eval/pipeline_report.py` |
| `eval_baseline.*` | the golden-set eval of the first version, the reference for `eval/run.py --compare` | `eval/run.py` |
| `history/` | every earlier golden-set run (`eval_v2a` ... `eval_v11`) and the holdout before the D26 rule, kept for the record | `eval/run.py`, `eval/holdout.py` |
