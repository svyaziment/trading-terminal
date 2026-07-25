# Project Context: Trading Terminal

> Last refreshed: 2026-07-25 (task-070a). Source: docs/refresh/current_tree.txt (git ls-files).
> This file is the canonical project context for agents. Keep it current.

## 1. Project Overview

Trading terminal for MOEX stocks. Sandbox mode (no real trading). Stack: FastAPI backend (Python 3.12), React frontend (Vite), PostgreSQL (external, via host.docker.internal from Docker). T-Bank Invest API for market data (gRPC, sandbox). 10 technical analysis patterns generate BUY/SELL signals. Deterministic backtest engine validates strategies on historical data.

## 2. File Structure
trading-terminal/
├── backend/
│ ├── app/
│ │ ├── api/
│ │ │ ├── market_data.py # T-Bank API: candles, instruments, top stocks
│ │ │ ├── data_refresh.py # POST /api/data/refresh (background, shared lock)
│ │ │ ├── signals_jobs.py # POST /api/signals/regenerate (background, shared lock)
│ │ │ ├── jobs_state.py # Shared in-process lock (refresh/regenerate/backtest)
│ │ │ ├── signals.py # GET /api/signals (legacy, may be unused)
│ │ │ ├── backtest_jobs.py # POST /api/backtest/run, GET /api/backtest/run/status (not yet committed)
│ │ │ └── moex_1min_loader.py # MOEX ISS API 1min candles loader (incremental, not yet committed)
│ │ ├── analytics/
│ │ │ ├── indicators_manager.py # 33 technical indicators (SMA, EMA, RSI, MACD, BB, ATR, etc.)
│ │ │ ├── signal_generator.py # Signal generation from patterns + indicators
│ │ │ ├── signal_engine.py # Applies patterns to indicator DataFrame
│ │ │ ├── candles_aggregator.py # 30min raw -> candles_aggregated (1h, 4h, 1d, 1w, 1M)
│ │ │ ├── candles_1min_aggregator.py # 1min raw -> candles_aggregated (30min, 1h, 4h, 1d), incremental (not yet committed)
│ │ │ ├── backtest_engine.py # Deterministic backtest engine (not yet committed)
│ │ │ ├── backtest_models.py # Backtest contract (BacktestParams, ExitRule, constants) (not yet committed)
│ │ │ ├── aggregate_candles.py # Legacy candle aggregation (early tasks)
│ │ │ ├── pipeline.py # Legacy pipeline orchestration (early tasks)
│ │ │ ├── run_generate_signals.py # Legacy signal generation script (early tasks)
│ │ │ ├── top_stocks.py # Top stocks by volume logic
│ │ │ └── patterns/ # 10 patterns: trend/, mean_reversion/, breakout/, volume/, price_action/
│ │ ├── core/
│ │ │ ├── config_manager.py # Settings (pydantic), logger, env vars
│ │ │ └── config.py # Legacy config (early tasks)
│ │ ├── db/
│ │ │ ├── db_manager.py # Synchronous PostgreSQL manager (pool, select, execute, insert_with_schema)
│ │ │ ├── audit_raw.py # Raw data audit utility
│ │ │ ├── check_db_connection.py # DB connection check utility
│ │ │ ├── check_external.py # External connectivity check utility
│ │ │ └── check_network.py # Network check utility
│ │ ├── broker/ # Legacy T-Bank broker loader (early tasks, unused in current pipeline)
│ │ │ ├── check_loader.py
│ │ │ └── data_loader.py
│ │ └── main.py # FastAPI app, route registration
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ ├── requirements.txt / requirements-dev.txt
│ └── tests/
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Main app (SignalsPanel, PipelineWidget)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Signals table (sort, filter, pagination)
│ │ │ ├── PipelineWidget.tsx # Refresh/regenerate status widget (polls /api/jobs/status)
│ │ │ ├── CandleChart.tsx # Candlestick chart component
│ │ │ ├── InstrumentsPanel.tsx # Instruments list panel
│ │ │ ├── PatternStatsPanel.tsx # Pattern statistics panel
│ │ │ ├── RegenerateWidget.tsx # Signal regeneration widget
│ │ │ ├── SignalDetailModal.tsx # Signal detail modal
│ │ │ └── TopStocksPanel.tsx # Top stocks by volume panel
│ │ ├── api.ts # API client (legacy name; client.ts in docs)
│ │ ├── types.ts # TypeScript types
│ │ ├── index.css / main.tsx
│ │ └── tailwind.config.js / vite.config.js
│ └── package.json
├── docs/
│ ├── agents/
│ │ ├── project-context.md # This file
│ │ ├── project-context.ru.md # Russian version
│ │ ├── handover.md # Agent handover guide
│ │ ├── handover.ru.md # Russian version
│ │ └── documentation-policy.md # Documentation policy
│ └── refresh/ # Refresh artifacts (file_scan_report.md, db_schema.md, current_tree.txt) (not yet committed)
├── scripts/ # Task scripts (gitignored)
│ ├── refresh/refresh-project-docs.sh # Refresh scanner wrapper
│ ├── targeted_project_scanner.py # File scanner
│ └── db_schema_scanner.py # DB schema scanner
├── docker-compose.yml # backend + frontend services
└── .env # Secrets (TINVEST_TOKEN, PSTGRS_PWD, etc.)

## 3. Database Schema (PostgreSQL, schema: trading)

| Table | Rows (approx) | Description |
|---|---|---|
| candles_30min_raw | ~28k | 30min candles from T-Bank API (30 tickers, ~1 month) |
| candles_1min_raw | ~900k | 1min candles from MOEX ISS API (SBER/GAZP/VTBR, 2 years) |
| candles_aggregated | ~100k | Aggregated candles (30min, 1h, 4h, 1d, 1w, 1M) |
| indicators | ~60k | 33 technical indicators per candle |
| signals | ~45k | BUY/SELL signals (10 patterns, confidence, total_signals) |
| instruments | ~4.3k | Ticker metadata (figi, lot_size, min_price_increment) |
| top_stocks_by_volume | 30 | Top 30 tickers by volume |
| backtest_runs | ~100 | Backtest run metadata (params, status, total_trades) |
| backtest_trades | ~200k | Individual trades (entry/exit, PnL, costs) |
| backtest_equity | ~200k | Equity curve per run |
| backtest_metrics | ~3k | Aggregated metrics per run/group (PF, expectancy, win_rate, benchmarks) |

Key columns:
- candles_1min_raw: ticker, figi, timestamp (PK: ticker, timestamp), open/high/low/close, volume
- backtest_runs: id, strategy_name, params (jsonb), universe_snapshot (jsonb), selection_bias, status, total_trades
- backtest_trades: run_id (FK), ticker, timeframe, signal_id, pattern_name, side (LONG), entry_ts/price, exit_ts/price, exit_reason, bars_held, gross/commission/slippage/net_return_pct, pnl_rub
- backtest_metrics: run_id (FK), group_key (ALL or pattern=X|tf=Y), n_trades, profit_factor, expectancy, win_rate, sharpe, sortino, max_drawdown, reliable, benchmark_buyhold/random_return_pct

## 4. Data Pipeline

1. **Market data fetch**: T-Bank Invest API (gRPC, sandbox) -> candles_30min_raw (30 tickers, 30min candles).
2. **1min candles fetch**: MOEX ISS API (REST) -> candles_1min_raw (SBER/GAZP/VTBR, 1min candles, 2 years). Incremental loader (backend/app/api/moex_1min_loader.py).
3. **Aggregation**: candles_30min_raw -> candles_aggregated (1h, 4h, 1d, 1w, 1M) via candles_aggregator.py. candles_1min_raw -> candles_aggregated (30min, 1h, 4h, 1d) via candles_1min_aggregator.py (incremental).
4. **Indicators**: candles_aggregated -> indicators (33 indicators per candle) via indicators_manager.py.
5. **Signals**: indicators + patterns -> signals (BUY/SELL, confidence, total_signals, pattern_name) via signal_generator.py + signal_engine.py.
6. **Backtest**: signals + candles_aggregated + indicators -> backtest_runs/trades/equity/metrics via backtest_engine.py + backtest_jobs.py.

## 5. API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Health check |
| GET | /api/candles | Candles (ticker, timeframe, limit) |
| GET | /api/instruments | Instruments list |
| GET | /api/top-stocks | Top 30 by volume |
| GET | /api/signals | Signals (ticker, timeframe, limit) |
| POST | /api/data/refresh | Background: fetch candles + aggregate + indicators + signals (shared lock) |
| POST | /api/signals/regenerate | Background: regenerate signals only (shared lock) |
| GET | /api/jobs/status | All jobs status (refresh, regenerate, backtest) |
| POST | /api/backtest/run | Background: run backtest matrix (shared lock). Params: quick, universe_limit, signal_exit, tickers |
| GET | /api/backtest/run/status | Backtest job status (progress, combo_results) |

Shared lock: jobs_state.py (in-process). Only one heavy job (refresh/regenerate/backtest) runs at a time. Others return 409.

## 6. Patterns (10 total)

| Category | Pattern | Description |
|---|---|---|
| Trend | Trend_SMA_Alignment | SMA alignment (20/50/200) |
| Mean Reversion | MR_RSI_Reversal | RSI reversal from overbought/oversold |
| Breakout | BO_BB_Squeeze | Bollinger Bands squeeze |
| Volume | VOL_Spike | Volume spike (>2x average) |
| Volume | VOL_Low_Pullback | Low-volume pullback |
| Price Action | PA_Hammer | Hammer (bullish reversal) |
| Price Action | PA_HangingMan | Hanging man (bearish reversal) |
| Price Action | PA_Engulfing | Engulfing (bullish/bearish) |
| Price Action | PA_ThreeWhiteSoldiers | Three white soldiers (bullish) |
| Price Action | PA_ThreeBlackCrows | Three black crows (bearish) |

## 7. Known Issues & Status

- **1d signals**: Now present (213-215 per ticker on 2-year history). Previously 0 due to insufficient 1d indicators (274 rows). Fixed by loading 2-year 1min history and aggregating to 1d.
- **Backtest results (2-year history, SBER/GAZP/VTBR)**: Rule-based strategies are NOT profitable after commission (0.06% round-trip). All 30 matrix combinations have PF < 1, expectancy < 0. Best: filter_ts2_c0.9_sigOn (PF 0.800, exp -0.066%). Strategy underperforms buy&hold and random on strong signals. Per-ticker: GAZP best PF (0.870), SBER worst (0.581), VTBR buy&hold strongly positive (+0.17%) but strategy loses (-0.08%). Exit by any signal (sigOn) better than exit by strong signal (sigOn_ts3) or no signal exit (sigOff).
- **Session timezone**: candles_aggregated timestamp timezone unverified (likely UTC). session_only exit forced False in backtest v1.
- **Universe bias**: Backtest uses fixed top-30 (selection_bias=true). Rolling universe not implemented.
- **Commission**: 0.06% round-trip (0.03% per side). Exchange fee not included separately.
- **Uncommitted files**: backtest_jobs.py, moex_1min_loader.py, candles_1min_aggregator.py, backtest_engine.py, backtest_models.py, docs/refresh/ are not yet in git (task-049..069 changes pending commit).

## 8. Roadmap Status

| Block | Description | Status |
|---|---|---|
| A | Core infrastructure (FastAPI, DB, T-Bank API) | Done |
| B | Indicators (33) | Done |
| C | Patterns (10) + signals | Done |
| D | Frontend (SignalsPanel, PipelineWidget) | Done |
| E | Background jobs (refresh, regenerate, shared lock) | Done |
| F | Documentation (project-context, handover, policy) | Done (this refresh) |
| G | Backtest engine + matrix | Done (task-049..065, not yet committed) |
| H | 1min candles (MOEX ISS API) + aggregation | Done (task-054..063, not yet committed) |
| I | ML (CatBoost/LightGBM) | Not started |
| J | Frontend backtest visualization | Not started |

## 9. Important Notes

- **Sandbox mode**: No real trading. T-Bank API sandbox tokens.
- **Secrets**: .env (TINVEST_TOKEN, PSTGRS_PWD). Never log secrets.
- **Docker**: Backend image must be rebuilt after code changes (docker compose up -d --build backend).
- **MSYS path conversion**: Use MSYS_NO_PATHCONV=1 for docker commands with absolute paths in Git Bash.
- **Logging**: DBManager logs to stdout by default. Reroute to stderr in scripts to keep stdout clean for JSON.
- **Backtest conclusion**: Rule-based strategies (patterns -> signals -> stop/take/holding) do NOT have edge after commission on MOEX top-3 (SBER/GAZP/VTBR) over 2 years. Signals carry weak directional info (buy&hold positive on strong signals), but rules of exit and commission kill it. Next steps: ML on indicators (not patterns), or revise hypothesis, or accept result.
- **Data history**: 1min candles loaded for SBER/GAZP/VTBR (2 years). Other 27 tickers still have ~1 month (from T-Bank API 30min raw). Expand via moex_1min_loader.py for full universe.
- **Legacy files**: broker/, aggregate_candles.py, pipeline.py, run_generate_signals.py, config.py, signals.py are from early tasks (task-000..031) and may be unused in current pipeline. Keep for reference; do not delete without verification.
