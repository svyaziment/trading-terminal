# Project Context: Trading Terminal

Last refreshed: 2026-08-16 (Issue #59 T-Bank Sandbox API integration). Source: docs/refresh/context_collector.py + git ls-files.
This file is the canonical project context for agents. Keep it current.

## 1. Project Overview

Trading terminal for MOEX stocks. Sandbox mode (no real trading). Stack: FastAPI backend (Python 3.12), React frontend (Vite + Tailwind + lightweight-charts), PostgreSQL (external, via host.docker.internal from Docker). Market data: T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1min candles).

Three pillars:
1. **Backtest / Strategy Lab** - parameterizable strategy engine (AND-patterns, multi-window confirmation, commission/slippage/RR, depth presets, bootstrap) + walk-forward validation, exposed via API and a constructor UI.
2. **Paper trading** - the active strategy from Strategy Lab (table `strategies`, `in_paper_test=true AND locked=true`) trades virtually via the unified `StrategyEvaluator` (single brain with backtest). Current: `test_20260731` (levels_reversal + signal_4h_buy, RR 1:2, confirm 10min, 28 tickers). Single arm: market entry, window mode (7-19 MSK), RR from config.

**Strategy Plugin System (Epic #39):** strategies are pluggable via `StrategyPlugin` ABC in `strategies/`. Registered plugins: `levels_reversal` (wrapper around `StrategyEvaluator`), `atr_reversal` (Zvezdin ATR reversal). `portfolio_simulator.py` provides shared-capital backtest (50k RUB, 10k slots, max 5 positions, volume-priority slot competition, GAME OVER at cash<=0).
3. **Frontend dashboards** - Signals, Strategy Lab (backtest constructor), Paper Trading (A/B monitoring with factor filters + PnL chart).

## 2. File Structure
trading-terminal/
├── backend/
│ ├── app/
│ │ ├── api/
│ │ │ ├── market_data.py # T-Bank API: candles, instruments, top stocks
│ │ │ ├── data_refresh.py # POST /api/data/refresh (background, shared lock)
│ │ │ ├── signals_jobs.py # POST /api/signals/regenerate (background, shared lock)
│ │ │ ├── jobs_state.py # Shared in-process lock (refresh/regenerate/backtest/strategy)
│ │ │ ├── backtest_jobs.py # POST /api/backtest/run (legacy pattern matrix)
│ │ │ ├── levels_backtest_jobs.py # Levels backtest matrix endpoints
│ │ │ ├── strategy_jobs.py # Strategy storage + backtest API (Strategy Lab)
│ │ │ ├── paper_trading_jobs.py # Paper trading monitoring API (overview/positions/dynamics)
│ │ │ ├── moex_1min_loader.py # MOEX ISS API 1min candles loader (incremental)
│ │ │ └── signals.py # GET /api/signals (legacy)
│ │ ├── analytics/
│ │ │ ├── indicators_manager.py # 33 technical indicators
│ │ │ ├── signal_generator.py # Signal generation from patterns + indicators
│ │ │ ├── signal_engine.py # Applies patterns to indicator DataFrame
│ │ │ ├── candles_aggregator.py # 30min raw -> candles_aggregated (1h,4h,1d,1w,1M)
│ │ │ ├── candles_1min_aggregator.py# 1min raw -> candles_aggregated (30min,1h,4h,1d), incremental
│ │ │ ├── data_refresher.py # Background: MOEX 1min + aggregation + indicators + signals
│ │ │ ├── backtest_engine.py # Deterministic backtest engine (legacy pattern matrix)
│ │ │ ├── backtest_models.py # Backtest contract (BacktestParams, ExitRule)
│ │ │ ├── levels_engine.py # 4h support/resistance levels + zones
│ │ │ ├── levels_backtest.py # Levels backtest (entry modes, confirmation, RR)
│ │ │ ├── levels_backtest_db.py # Levels backtest persistence
│ │ │ ├── levels_refresher.py # Levels refresh
│ │ │ ├── strategy_backtest.py # Parameterizable strategy engine + walk-forward (Strategy Lab)
│   │   ├── strategy_context.py      # Build strategy context (levels, ATR, BUY signals)
│ │ │ ├── trading_config.py # SINGLE SOURCE OF TRUTH: trading universe + strategy registry
│ │ │ ├── online_data.py # Streaming: 1min candles + order book -> online_* tables
│ │ │ ├── online_signals.py # Online signal engine (paper trading, A/B arms)
│   │   ├── pattern_registry.py      # Pattern registry + normalize_patterns (Epic #11)
│ │ │ ├── paper_trader.py # Paper trading engine (market+limit, stop/take, equity)
│   │   ├── paper_strategy.py        # Active paper strategy reader (from trading.strategies)
│   │   ├── strategies/            # StrategyPlugin architecture (Epic #39)
│   │   │   ├── base.py            # StrategyPlugin ABC + EntrySignal/ExitSignal/Position
│   │   │   ├── context.py         # MarketContext dataclass
│   │   │   ├── registry.py        # StrategyRegistry + register_default_strategies
│   │   │   ├── levels_reversal.py # LevelsReversalStrategy (wrapper around StrategyEvaluator)
│   │   │   └── atr_reversal.py    # ATR reversal strategy (Zvezdin)
│   │   ├── portfolio_backtest.py  # Strategy-agnostic backtest via StrategyPlugin
│   │   ├── portfolio_simulator.py # Shared-capital portfolio simulator (50k/10k slots, GAME OVER)
│   │   ├── atr_backtest.py        # ATR strategy backtest framework
│ │ │ ├── position_catchup.py # Startup catch-up of pending/open positions
│ │ │ ├── top_stocks.py # Top stocks by volume logic
│ │ │ └── patterns/ # 10 patterns: trend/, mean_reversion/, breakout/, volume/, price_action/
│ │ ├── core/config_manager.py # Settings (pydantic), logger, env vars
│ │ ├── broker/
│ │ │ ├── data_loader.py # Historical candles via T-Bank Invest API
│ │ │ └── tinkoff_sandbox.py # Sandbox-only orders, balance, positions, cancellation
│ │ ├── db/db_manager.py # Synchronous PostgreSQL manager (pool, select, execute, insert_with_schema)
│ │ └── main.py # FastAPI app, route registration
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ └── tests/
│       ├── test_strategy_plugin.py    # Bit-for-bit regression test (levels_reversal)
│       └── test_portfolio_simulator.py # Portfolio simulator unit + integration tests
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Main app (tabs: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Signals table (sort, filter, pagination)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: backtest constructor + results + lock UI
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (filters, PnL chart, positions)
│   │   ├── PatternSettingsModal.tsx # Schema-driven pattern settings modal (Epic #11)
│ │ │ ├── PipelineWidget.tsx # Refresh/regenerate status widget
│ │ │ ├── CandleChart.tsx # Candlestick chart
│ │ │ ├── InstrumentsPanel.tsx # Instruments list
│ │ │ ├── PatternStatsPanel.tsx # Pattern statistics
│ │ │ ├── SignalDetailModal.tsx # Signal detail modal
│ │ │ └── TopStocksPanel.tsx # Top stocks by volume
│ │ ├── api.ts # API client
│ │ ├── types.ts # TypeScript types
│ │ └── index.css / main.tsx
│ └── package.json / tailwind.config.js / vite.config.js
├── analytics/ # Git-tracked, published analytical results
│ └── issue-44-strategy-comparison/ # Notebook, report, metrics, and plots
├── docs/
│ ├── agents/ # project-context.md, handover.md (+ .ru versions), documentation-policy.md
│ ├── strategy/ # levels-reversal-strategy.md, paper-trading.md, testing-rules.md, backtest-report.md (+ .ru)
│ └── refresh/context_collector.py # Context collector for agent tasks
├── scripts/ # Task scripts (gitignored) + refresh scanners
├── start_processes.sh # Start paper trading processes (catch-up + 4 processes)
├── stop_processes.sh # Stop paper trading processes
└── docker-compose.yml # agent + backend services (backend mounts ./reports)

## 3. Database Schema (PostgreSQL, schema: trading)

| Table | Rows (approx) | Description |
|---|---|---|
| candles_30min_raw | ~28k | 30min candles from T-Bank API (30 tickers, ~1 month) |
| candles_1min_raw | ~14.6M | 1min candles from MOEX ISS API (top-15+, 2 years) |
| candles_aggregated | ~384k | Aggregated candles (30min, 1h, 4h, 1d) |
| indicators | ~210k | 33 technical indicators per candle |
| signals | ~170k | BUY/SELL signals (10 patterns, confidence, total_signals) |
| instruments | ~4.3k | Ticker metadata (figi, lot_size, min_price_increment) |
| top_stocks_by_volume | 30 | Top 30 tickers by volume |
| online_candles_1min | streaming | Live 1min candles (streaming) |
| online_orderbook_aggregates | streaming | Live order book aggregates (bid/ask depth, volume_imbalance) |
| **strategies** | ~15 | Strategy Lab: name, config (jsonb), in_paper_test, locked, description |
| **backtest_results** | ~64 | Strategy Lab: per-ticker backtest/walk-forward metrics (jsonb) |
| **paper_positions** | ~704 | Paper trading positions (A/B factors, limit/market, stop/take, PnL) |
| **paper_equity** | ~2855 | Equity curve (equity_rub, realized_pnl, drawdown_pct, open_positions) |
| **trading_universe** | 15 | Traded universe (ticker, rank, pf, source) - top-15 by PF |
| **alerts** | ~72 | Online signals (details jsonb: price, support/take, factors) |
| backtest_runs | ~300 | Backtest run metadata (legacy + levels matrix) |
| backtest_trades | ~200k | Individual trades (legacy matrix) |
| backtest_equity | ~200k | Equity curve per run (legacy matrix) |
| backtest_metrics | ~6.3k | Aggregated metrics per run/group (PF, expectancy, win_rate, benchmarks) |

Key columns (new tables):
- `strategies`: id, name (unique), config (jsonb: patterns, confirm_windows, commission_pct, slippage_pct, risk_reward, n_runs), in_paper_test (bool), locked (bool), description
- `backtest_results`: id, strategy_id (FK), ticker, test_type (full_sample/walkforward), depth, metrics (jsonb), created_at
- `paper_positions`: id, ticker, entry_ts/price, stop_price, take_price, limit_price, limit_ts, size_lots, size_rub, lot_size, status (pending/open/closed_stop/closed_take/cancelled), signal_source, window_mode, rr_mode, rr_ratio, entry_mode (market/limit), signal_id, strategy_name, exit_ts/price/reason, pnl_rub, pnl_pct
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 4. Data Pipeline

**Historical / refresh** (`data_refresher.py`, background, every 15 min, top-15 universe):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Keeps 4h BUY signals fresh for the base_4hbuy arm.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
- paper_trader: reads strategy config from DB (RR from `config.risk_reward`), alerts -> market positions (open at best_ask, single arm) -> monitor stop/take -> write equity. Records `strategy_name` in `paper_positions`.
- On startup `start_processes.sh` runs `position_catchup.py` (resolve pending + check open against historical 1min candles).

**Strategy Lab** (`strategy_backtest.py` via `strategy_jobs.py`): config -> per-ticker backtest (full-sample) + walk-forward (half-years 2024-H2..2026-H1) -> backtest_results.

## 5. API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | /health | Health check |
| GET | /api/candles | Candles (ticker, timeframe, limit) |
| GET | /api/instruments | Instruments list |
| GET | /api/top-stocks-by-volume | Top 30 by volume |
| GET | /api/signals | Signals (ticker, timeframe, limit, filters, pagination) |
| GET | /api/signals/stats | Signal statistics |
| POST | /api/data/refresh | Background: fetch + aggregate + indicators + signals (shared lock) |
| POST | /api/signals/regenerate | Background: regenerate signals (shared lock) |
| GET | /api/jobs/status | All jobs status |
| POST | /api/backtest/run | Background: legacy pattern matrix backtest |
| POST | /api/levels-backtest/run | Levels backtest matrix |
| POST | /api/strategies | Save strategy (rejects overwrite of locked) |
| GET | /api/strategies | List strategies (with in_paper_test/locked/description) |
| GET | /api/strategies/run/status | Strategy backtest job status |
| GET | /api/strategies/data-range | Min/max date of candles_1min_raw (for date pickers) |
| POST | /api/strategies/{id}/run | Run backtest (full_sample/walkforward, depth or custom date_from/date_to) |
| GET | /api/strategies/{id}/results | Backtest results (per-ticker metrics) |
| GET | /api/tickers/big | Tickers with >= N 1min candles (selectable universe) |
| GET | /api/paper-trading/overview | Strategy name + factor options + summary stats (factor filters) |
| GET | /api/paper-trading/positions | Positions list (filters + pagination + sort) |
| GET | /api/paper-trading/dynamics | Cumulative realized PnL series by 1h/1d/1w (factor filters) |

Shared lock: jobs_state.py (in-process). Only one heavy job runs at a time; others return 409.

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

Strategy Lab patterns (config-driven, AND logic): `levels_reversal` (4h support zone + confirmation), `signal_4h_buy` (active 4h BUY), `rsi_oversold`, `macd_bullish`, `bb_lower`. `levels_reversal` is required (defines stop/take); the others are AND-filters.

## 7. Known Issues & Status

- **Active paper strategy**: `test_20260731` (id=36 in `trading.strategies`, `in_paper_test=true`, `locked=true`). Config: levels_reversal (4h, swing+impulse, window 10, body 0.7, impulse 1.5, zone 0.5) + signal_4h_buy, confirm [10], RR 1:2, commission 0.06%. Universe: 28 tickers from run_params. Verified: 72 signals emitted, 62 positions opened, first closed trade PnL +0.77%. Previous validated strategy `levels_reversal_4hbuy` remains in trading_config.py as reference.
- **Legacy pattern-matrix backtest**: rule-based strategies NOT profitable after commission on MOEX top-3 over 2 years (all PF < 1). Superseded by the levels approach.
- **Universe**: top-15 by PF (trading_universe), single source of truth via trading_config.get_trading_universe(). All background modules use it.
- **Session timezone**: candles timestamps are naive (assumed MSK for trading logic). session_only forced False in backtest v1.
- **Commission**: 0.06% round-trip (0.03%/side). Exchange fee not included separately.
- **1d indicators**: need >=200 candles; some tickers have fewer (warning, skipped).

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
| P | Live Trading Infrastructure (sandbox execution, risk controls, control panel) | In progress: sandbox broker client done (#59) |

## 9. Important Notes

- **Sandbox mode**: no real trading. T-Bank API sandbox tokens.
- **Secrets**: .env (TINVEST_TOKEN, TINVEST_ACC, PSTGRS_PWD). Never log secrets.
- **Docker**: rebuild backend image after code changes (`docker compose up -d --build backend`). Backend mounts `./reports` (for last_run.json).
- **Single source of truth**: trading universe + strategy definitions live in `trading_config.py` / `trading.trading_universe`. Do not hardcode ticker lists or strategy params in modules.
- **Locked strategy**: the strategy under paper test has `locked=true`; the API rejects overwriting it (409). Unlock only after the test period.
- **Logging**: DBManager logs to stdout by default. Reroute to stderr in scripts that parse JSON from stdout. Background processes (start_processes.sh) use `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` for unbuffered logging to log files.

## 10. T-Bank Sandbox API Integration

`backend/app/broker/tinkoff_sandbox.py` is the execution boundary for Epic #58. `TinkoffSandboxClient` uses only `client.sandbox`; it never calls the production `orders` service. It provides:
- `execute_order` for market and limit orders (quantity is in lots);
- `check_balance` for free cash by currency;
- `get_positions` for non-zero open portfolio positions;
- `cancel_order` for active sandbox orders.

Operational policy is centralized in `analytics/trading_config.py` (`SANDBOX_TRADING`): sandbox enablement, hard prohibition of real trading, initial capital reference, default currency, retry count/backoff, and account discovery. Secrets are not stored there: `TINVEST_TOKEN` and optional `TINVEST_ACC` continue to be loaded through `core/config_manager.py`.

Transient gRPC failures (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`) use exponential backoff. Order retries reuse the same idempotency `order_id`, preventing duplicate execution after an uncertain response. If `TINVEST_ACC` is empty or points to a non-sandbox account (`50004`), the client falls back to the first open sandbox account and caches its id; it does not create or fund accounts automatically.
