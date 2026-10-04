# Data Pipeline

> **Source:** project-context.md sections 4
> **Last refreshed:** 2026-10-04, task-346

## 4. Data Pipeline

**Historical / refresh** (`data_refresher.py`, background, every 15 min, `get_streaming_universe()` = top-15 ∪ LIVE_UNIVERSE):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Keeps 4h BUY signals fresh for the base_4hbuy arm.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
- paper_trader: reads strategy config from DB (RR from `config.risk_reward`, trailing from `config.trailing_stop`), alerts -> market positions -> monitor stop/take/trailing (dynamically updates `stop_price` via in-memory `TrailingState`, emits `trailing` exit reason) -> write equity. Records `strategy_name` in `paper_positions` and sends best-effort Telegram alerts for opens, closes, stop/take, drawdown threshold crossings, and GAME OVER.
- On startup `start_processes.sh` runs `position_catchup.py` (resolve pending + check open against historical 1min candles).

**Sandbox live execution** (`live_executor.py`, opt-in background process): uses the same `StrategyEvaluator` and live 1min context, then requires a MOEX entry window [10:00, 19:00) MSK, fresh order-book imbalance, checks sandbox cash, calculates whole-lot size, submits a market BUY, and records the position in `trading.live_positions`. Overnight start: `START_LIVE_EXECUTOR=1 ./start_processes.sh` (no `DURATION_MINUTES`) waits until 10:00 MSK, enters only until 19:00, and keeps stop/take until the position closes by price. Normal paper startup does not place broker orders.

**Strategy Lab** (`strategy_jobs.py`): the Lab UI always stores `config.strategy_name = "levels_reversal"`, so full-sample runs go through `run_portfolio_backtest` (plugin), not `run_strategy_backtest`. Plugin `MarketContext` must include `htf_bars` from `build_strategy_context` so `LevelsTracker` sees closed HTF bars (Issue #116). Walk-forward still uses `run_walkforward`. Metrics JSONB is sanitized (`inf`/`nan` → `null`) before INSERT into `backtest_results`.
