# Issue #147 — Production trailing-stop parity gate

Analytics package that proves the production trailing-stop implementation
(Issue #145) reproduces the frozen #139 analytics book B.

## Structure
- `run.py` — main script (extract + report stages)
- `run.md` — run protocol with documented tolerances
- `report.md` — generated parity report (after run)
- `report.json` — machine-readable parity results
- `extract.json` — raw production book results (after extract stage)

## Frozen references (DO NOT MODIFY)
- `analytics/issue-139-trailing-stop-new-level/` — book A/B reference
- `analytics/issue-143-trailing-robustness/` — grid robustness reference

## Key invariants
1. Strategy 126 config is READ-ONLY (SELECT only, no writes).
2. Protected strategies (126/36/102/118) verified before and after.
3. No writes to `paper_positions`, `backtest_results`, or any trading table.
4. ref139 grid is injected EXPLICITLY (not from defaults).
5. ultra_late_tight comes from `trading_config.TRAILING_STOP` defaults.
