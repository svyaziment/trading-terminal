# Roadmap Status

> **Source:** project-context.md sections 8
> **Last refreshed:** 2026-10-04, task-346

## 8. Roadmap Status

| Block | Description | Status |
|---|---|---|
| A | Core infrastructure (FastAPI, DB, T-Bank API) | Done |
| B | Indicators (33) | Done |
| C | Patterns (10) + signals | Done |
| D | Frontend (SignalsPanel, PipelineWidget) | Done |
| E | Background jobs (refresh, regenerate, shared lock) | Done |
| F | Documentation (project-context, handover, policy) | Done (this refresh) |
| G | Backtest engine + pattern matrix | Done (legacy) |
| H | 1min candles (MOEX ISS) + aggregation | Done |
| K | Levels engine + levels backtest + matrix | Done |
| L | Strategy Lab (parameterizable engine + walk-forward + storage + UI) | Done |
| M | Paper trading (parameterized strategy from Strategy Lab, single arm market) | Done (verified: 72 signals, 62 positions) |
| N | Trading universe (top-15 by PF, single source of truth) | Done |
| I | ML (CatBoost/LightGBM) | Not started |
| J | A/B test analysis report (signal_source x window x rr x entry) | Pending (accumulate closed trades) |
| O  | Strategy Plugin System (StrategyPlugin ABC + registry + portfolio simulator) | Done (Epic #39) |
| P | Live Trading Infrastructure (sandbox execution, market filters, risk controls, alerting, control panel) | Backend execution #59-#62, Telegram #64, monitoring panel #65, live-universe #66, skip-reason logging #73, and first sandbox canary #74 done |
| Q | SignalEngine patterns in Strategy Lab (Epic #78) | #79–#82 done (evaluator, registry schemas, E2E/docs, Lab UI grouping) |
| R | Pattern chart preview in Lab + Signals (Epic #87) | #88 preview API + levels overlays done; #89–#92 pending |
| S | Level Breakout & Role Reversal (Epic #105) | #106 LevelsTracker + #107 `level_breakout_retest` AND-filter + #109 Lab chip done; analytics validation and optional preview pending |
| T | Composite S/R pattern (Epic #115) | #116 Lab/plugin HTF + JSONB Infinity + #117 `levels_sr_breakout` + #118 Lab chip + #119 AFKS smoke + #124 Lab-universe A/B done |
| U | Support with tracker (Epic #126) | #127 `levels_sr_support` backend + #128 Lab chip + #129 isolated Lab universe + #130 portfolio #44 done |
| V | Stepped trailing-stop analytics (Issue #139) | Done — analytics-only A/B (fixed 1:3 vs stepped trailing) on locked `test_20260830_new_level` id=126, RR 1:3; trailing lives in `analytics/.../trailing.py`, not wired into the production exit path |
| W | Stepped trailing stop in production (Epic #142) | #144 contract done, #145 engine/plugin/simulator/walk-forward done, #146 Lab editor done, #148 paper trading integration done. #147 parity gate escalated, #151 sandbox live pending. |
