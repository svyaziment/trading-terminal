# Glossary

> **Source:** project documentation (task-346)
> **Last refreshed:** 2026-10-04, task-346
> **Related:** `00-architecture/overview.md`, `03-strategies/strategy-lab.md`, `07-operations/runbook.md`

| Term | Definition | Where to read |
|---|---|---|
| Trading terminal | Paper trading system for MOEX stocks (no real trading). Stack: FastAPI (Python 3.12) + React + PostgreSQL. | `00-architecture/overview.md` |
| Paper trading | Simulation of trading without real money; positions and orders live in the local DB. | `00-architecture/overview.md` |
| Sandbox | T-Bank Invest API mode without real funds; the whole terminal is a sandbox. | `01-data-pipeline/broker-streaming.md` |
| Backtest | Historical replay of a strategy against candles to evaluate PnL / MaxDD. | `03-strategies/strategy-lab.md` |
| Strategy Lab | Module for creating, testing and running strategies. | `03-strategies/strategy-lab.md` |
| Locked strategy | Strategy with `locked=true` (126 / 36 / 102 / 118); content is immutable, reference only. | `03-strategies/trading-universe.md` |
| Trading universe | Set of tradable MOEX tickers; managed in `trading_config.py` (no hardcoded ticks). | `03-strategies/trading-universe.md` |
| Pattern / pattern features | Shape-based entry signals (e.g. `ultra_late_tight`); features computed on candles. | `03-strategies/pattern-features.md` |
| Trailing stop | Exit order that follows the price along levels (steps) to protect unrealised profit. | `03-strategies/trailing-stop.md` |
| Level / step | One of the discrete trailing steps; the stop moves in steps, not continuously. | `03-strategies/trailing-stop.md` |
| Ratchet | One-way behaviour: the stop can move only in the favourable direction and never back. | `03-strategies/trailing-stop.md` |
| S / R levels | Stop (S) and take-profit (R) levels of a position. | `03-strategies/trailing-stop.md` |
| Risk gate | Check that must pass before an order / position action (limits, exposure, state). | `05-risk-management/risk-gates.md` |
| Equity rules | Rules for position sizing and portfolio limits based on equity. | `05-risk-management/equity-rules.md` |
| Live executor | Component that turns strategy signals into paper orders via the broker layer. | `04-execution/live-executor.md` |
| Broker layer | Abstraction over T-Bank Invest API (gRPC): orders, positions, account state. | `04-execution/broker-layer.md` |
| Slot | Time bucket in the execution / telemetry pipeline (one slot = one tick of processing). | `07-operations/live-telemetry.md` |
| SignalEngine | Component that generates entry signals from patterns + filters. | `03-strategies/pattern-features.md` |
| AND filter | Multi-condition filter: all conditions must hold for a signal to fire. | `03-strategies/pattern-features.md` |
| Walk-forward | Backtest variant where the strategy is re-validated on a rolling forward window. | `03-strategies/strategy-lab.md` |
| Candle | 1-minute candle (MOEX ISS API) — the base market-data unit. | `01-data-pipeline/candle-pipeline.md` |
| PnL | Profit and loss of a position or the portfolio. | `02-database/schema.md` |
| MaxDD | Maximum drawdown of an equity curve. | `03-strategies/strategy-lab.md` |
| T-Bank Invest API | Broker API (gRPC, sandbox) — orders, positions, quotes. | `01-data-pipeline/broker-streaming.md` |
| MOEX ISS API | Exchange API (REST) — market data, 1-minute candles. | `01-data-pipeline/candle-pipeline.md` |
| Runbook | Operational playbook: start / stop / check / recover services. | `07-operations/runbook.md` |
