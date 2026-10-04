# Project Overview

> **Source:** project-context.md sections 1
> **Last refreshed:** 2026-10-04, task-346

## 1. Project Overview

Trading terminal for MOEX stocks. Sandbox mode (no real trading). Stack: FastAPI backend (Python 3.12), React frontend (Vite + Tailwind + lightweight-charts), PostgreSQL (external, via host.docker.internal from Docker). Market data: T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1min candles).

Three pillars:
1. **Backtest / Strategy Lab** - parameterizable strategy engine (AND-patterns, multi-window confirmation, commission/slippage/RR, depth presets, bootstrap) + walk-forward validation, exposed via API and a constructor UI.
2. **Paper trading** - the active strategy from Strategy Lab (table `strategies`, `in_paper_test=true AND locked=true`) trades virtually via the unified `StrategyEvaluator` (single brain with backtest). Current: `test_20260830_new_level` (levels_sr_support + signal_4h_buy, RR 1:3, confirm 10min, 28 Lab tickers). Previous locked row `test_20260731` is unlocked and kept as reference. Single arm: market entry, window mode (7-19 MSK), RR from config.

**Strategy Plugin System (Epic #39):** strategies are pluggable via `StrategyPlugin` ABC in `strategies/`. Registered plugins: `levels_reversal` (wrapper around `StrategyEvaluator`), `atr_reversal` (Zvezdin ATR reversal). `portfolio_simulator.py` provides shared-capital backtest (50k RUB, 10k slots, max 5 positions, volume-priority slot competition, GAME OVER at cash<=0).
3. **Frontend dashboards** - Signals, Strategy Lab (backtest constructor), Paper Trading (A/B monitoring with factor filters + PnL chart).
