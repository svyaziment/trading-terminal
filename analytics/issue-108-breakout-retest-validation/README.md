# Issue #108: level_breakout_retest validation

Published Lab validation for [Issue #108](https://github.com/svyaziment/trading-terminal/issues/108)
(Epic #105). Baseline A is Issue #100 `test_20260820`. Config B adds
`level_breakout_retest`. Locked `test_20260731` is not rewritten.

## Reproduce

From the repository root (PostgreSQL required for a fresh run):

```bash
python analytics/issue-108-breakout-retest-validation/generate_inputs.py --workers 4 --skip-wf
python analytics/issue-108-breakout-retest-validation/analysis.py
```

`--reuse-a` (default) loads published Issue #100 `results.json` as config A. `--run-a` re-runs A. `--alrs-only` refreshes the ALRS case without backtests. Jobs checkpoint to `reports/Arctic/108_breakout-retest-validation/jobs.jsonl` (gitignored).

`generate_inputs.py` writes `reports/Arctic/108_breakout-retest-validation/results.json`
(gitignored). `analysis.py` copies a slim published `results.json` / `summary.json`
and the markdown/plots committed here.

Smoke:

```bash
python analytics/issue-108-breakout-retest-validation/generate_inputs.py --tickers ALRS --skip-wf --workers 1
```
