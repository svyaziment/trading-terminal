# Project Context: Trading Terminal

Last refreshed: 2026-08-29 (Issue #116 Lab/plugin HTF feed for LevelsTracker). Source: docs/refresh/context_collector.py + git ls-files.
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
│ │ │ ├── paper_trading_jobs.py # Paper monitoring API (prices, filters, positions/dynamics)
│ │ │ ├── notifications.py # Cached Telegram Bot API connectivity status
│ │ │ ├── live_trading_jobs.py # Sandbox live positions and PnL dynamics API
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
│ │ │ ├── levels_engine.py # 4h S/R levels + zones; overlapping_resistance_zone_at veto (#97); LevelsTracker (#106); is_broken veto skip (#107)
│ │ │ ├── levels_backtest.py # Levels backtest (entry modes, confirmation, RR)
│ │ │ ├── levels_backtest_db.py # Levels backtest persistence
│ │ │ ├── levels_refresher.py # Levels refresh
│ │ │ ├── strategy_backtest.py # Parameterizable strategy engine + walk-forward (Strategy Lab)
│   │   ├── strategy_context.py      # Build strategy context (levels, ATR, BUY signals, htf_bars)
│ │ │ ├── trading_config.py # SINGLE SOURCE OF TRUTH: universe, live top-5, strategies, live risk policy, LEVEL_STATE_MACHINE (#106), LEVEL_BREAKOUT_RETEST (#107)
│ │ │ ├── position_sizer.py # Hybrid risk/concentration sizing + lot rounding
│ │ │ ├── live_executor.py # Sandbox execution, protection, reconciliation, shutdown
│ │ │ ├── live_executor_preflight.py # Read-only checks before a sandbox canary
│ │ │ ├── online_data.py # Streaming: 1min candles + order book -> online_* tables
│ │ │ ├── orderbook_imbalance.py # Bid/ask depth ratio + mandatory live filter
│ │ │ ├── online_signals.py # Online signal engine (paper trading, A/B arms)
│   │   ├── pattern_registry.py      # Pattern registry + normalize_patterns (Epic #11); SignalEngine schemas + timeframe (#80)
│   │   ├── signal_pattern_filters.py # Inline SignalEngine AND-filters for StrategyEvaluator (Issue #79)
│ │ │ ├── paper_trader.py # Paper trading engine (market+limit, stop/take, equity)
│   │   ├── paper_strategy.py        # Active paper strategy reader (from trading.strategies)
│   │   ├── strategies/            # StrategyPlugin architecture (Epic #39)
│   │   │   ├── base.py            # StrategyPlugin ABC + EntrySignal/ExitSignal/Position
│   │   │   ├── context.py         # MarketContext dataclass (htf_bars for LevelsTracker, #116)
│   │   │   ├── registry.py        # StrategyRegistry + register_default_strategies
│   │   │   ├── levels_reversal.py # LevelsReversalStrategy (wrapper around StrategyEvaluator)
│   │   │   └── atr_reversal.py    # ATR reversal strategy (Zvezdin)
│   │   ├── portfolio_backtest.py  # Strategy-agnostic backtest via StrategyPlugin
│   │   ├── portfolio_simulator.py # Shared-capital portfolio simulator (50k/10k slots, GAME OVER)
│   │   ├── atr_backtest.py        # ATR strategy backtest framework
│ │ │ ├── position_catchup.py # Startup catch-up of pending/open positions
│ │ │ ├── top_stocks.py # Top stocks by volume logic
│ │ │ └── patterns/ # 10 SignalEngine modules + Lab level_breakout_retest.py (#107; not under breakout/)
│ │ ├── core/config_manager.py # Settings (pydantic), logger, env vars
│ │ ├── notifications/
│ │ │ └── telegram_notifier.py # Rate-limited paper-trading Bot API alerts
│ │ ├── broker/
│ │ │ ├── data_loader.py # Historical candles via T-Bank Invest API
│ │ │ └── tinkoff_sandbox.py # Sandbox-only orders, balance, positions, cancellation
│ │ ├── db/db_manager.py # Synchronous PostgreSQL manager (pool, select, execute, insert_with_schema)
│ │ └── main.py # FastAPI app, route registration
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ ├── migrations/ # Idempotent PostgreSQL migrations (including live_positions)
│ └── tests/
│       ├── test_strategy_plugin.py    # Bit-for-bit regression test (levels_reversal)
│       ├── test_resistance_zone_veto.py # Issue #97 ALRS #711 opposing-zone guard
│       ├── test_levels_state_machine.py # Issue #106 breakout / confirmation / veto skip
│       ├── test_level_breakout_retest.py # Issue #107 retest AND-filter / stop-take / veto skip
│       ├── test_issue100_analysis.py # Issue #100 Lab universe/veto/baseline helpers
│       └── test_portfolio_simulator.py # Portfolio simulator unit + integration tests
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Main app (tabs: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Signals table (sort, filter, pagination)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: chips from GET /api/patterns (#82/#109)
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (filters, PnL chart, positions)
│ │ │ ├── LiveTradingPanel.tsx # Live monitoring: open/history/equity/Telegram
│   │   ├── PatternSettingsModal.tsx # Schema-driven pattern settings modal (Epic #11; min/max errors #109)
│ │ │ ├── PipelineWidget.tsx # Refresh/regenerate status widget
│ │ │ ├── CandleChart.tsx # Candlestick chart
│ │ │ ├── InstrumentsPanel.tsx # Instruments list
│ │ │ ├── PatternStatsPanel.tsx # Pattern statistics
│ │ │ ├── SignalDetailModal.tsx # Signal detail modal
│ │ │ └── TopStocksPanel.tsx # Top stocks by volume
│ │ ├── api.ts # API client
│ │ ├── types.ts # TypeScript types (incl. LevelBreakoutRetestConfig)
│ │ ├── patternLab.ts # Chip grouping + RU/EN labels from GET /api/patterns (#82/#109)
│ │ ├── patternValidation.ts # Schema min/max checks before Lab save/run (#109)
│ │ └── index.css / main.tsx
│ └── package.json / tailwind.config.js / vite.config.js
├── analytics/ # Git-tracked, published analytical results
│ ├── issue-44-strategy-comparison/ # Notebook, report, metrics, and plots
│ ├── issue-66-live-universe/ # Live top-5 ranking, report, and plots
│ ├── issue-100-test-20260820-portfolio/ # Portfolio replay of Lab test_20260820 after #97 veto
│ ├── issue-100-test-20260820-resistance-veto/ # Lab full-sample + walk-forward of test_20260820 after #97 veto
│ └── issue-103-test-20260821-portfolio/ # Portfolio replay of Lab test_20260821 after #97 veto (swing+impulse)
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
| **live_positions** | runtime | T-Bank Sandbox orders, protection IDs, lifecycle, and realized PnL |
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
- `live_positions`: id, ticker, instrument_id, signal_ts, entry_price, lot_size, size_lots, stop_price, take_price, broker_order_id/stop_id/take_id, status, strategy_name, exit_ts/price/reason, pnl_rub
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 4. Data Pipeline

**Historical / refresh** (`data_refresher.py`, background, every 15 min, top-15 universe):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Keeps 4h BUY signals fresh for the base_4hbuy arm.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
- paper_trader: reads strategy config from DB (RR from `config.risk_reward`), alerts -> market positions (open at best_ask, single arm) -> monitor stop/take -> write equity. Records `strategy_name` in `paper_positions` and sends best-effort Telegram alerts for opens, closes, stop/take, drawdown threshold crossings, and GAME OVER.
- On startup `start_processes.sh` runs `position_catchup.py` (resolve pending + check open against historical 1min candles).

**Sandbox live execution** (`live_executor.py`, opt-in background process): uses the same `StrategyEvaluator` and live 1min context, then requires fresh order-book imbalance, checks sandbox cash, calculates whole-lot size, submits a market BUY, and records the position in `trading.live_positions`. Start explicitly with `START_LIVE_EXECUTOR=1 ./start_processes.sh`; normal paper startup does not place broker orders.

**Strategy Lab** (`strategy_jobs.py`): the Lab UI always stores `config.strategy_name = "levels_reversal"`, so full-sample runs go through `run_portfolio_backtest` (plugin), not `run_strategy_backtest`. Plugin `MarketContext` must include `htf_bars` from `build_strategy_context` so `LevelsTracker` sees closed HTF bars (Issue #116). Walk-forward still uses `run_walkforward`. Metrics JSONB is sanitized (`inf`/`nan` → `null`) before INSERT into `backtest_results`.

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
| GET | /api/patterns | Pattern registry schemas (Strategy Lab) |
| POST | /api/patterns/preview | Pattern chart preview: candles + typed overlays (`ray`, `band`, `line`, `marker`); #88 implements `levels_reversal` |
| POST | /api/strategies | Save strategy (rejects overwrite of locked) |
| GET | /api/strategies | List strategies (with in_paper_test/locked/description) |
| GET | /api/strategies/run/status | Strategy backtest job status |
| GET | /api/strategies/data-range | Min/max date of candles_1min_raw (for date pickers) |
| POST | /api/strategies/{id}/run | Run backtest (full_sample/walkforward, depth or custom date_from/date_to) |
| GET | /api/strategies/{id}/results | Backtest results (per-ticker metrics) |
| GET | /api/tickers/big | Tickers with >= N 1min candles (selectable universe) |
| GET | /api/paper-trading/overview | Strategy name + factor options + summary stats (factor filters) |
| GET | /api/paper-trading/positions | Positions list (filters + pagination + sort); open rows include current price and unrealized PnL |
| GET | /api/paper-trading/dynamics | Cumulative realized PnL series by 1h/1d/1w (factor/ticker/date filters) |
| GET | /api/notifications/status | Cached Telegram configuration and Bot API connectivity status |
| GET | /api/live-trading/positions | Sandbox live positions with current price, PnL, filters, sorting, and pagination |
| GET | /api/live-trading/dynamics | Cumulative realized sandbox PnL by 1h/1d/1w |

Shared lock: jobs_state.py (in-process). Only one heavy job runs at a time; others return 409.

## 6. Patterns (10 total)

The Signals tab still generates the ten `BasePattern` classes below into `trading.signals` (confidence, BUY/SELL, total_signals). That table is **not** the Lab filter path except for `signal_4h_buy`.

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

Strategy Lab patterns (config-driven, AND logic, same config for backtest / paper / live):
- `levels_reversal` — required; 4h support zone + confirmation; defines stop/take. Issue #97: `check_entry` rejects the bar when the 1min close sits in an active resistance zone (`overlapping_resistance_zone_at`); this is a defect, not role-reversal. Issue #106: when a `state` column is present, the veto skips non-`active` zones. Issue #107: `StrategyEvaluator` passes `LevelsTracker` into the veto only when `level_breakout_retest` is enabled; `build_levels` still has no `state`, so locked `test_20260731` stays bit-for-bit.
- `level_breakout_retest` — Lab AND-filter after `levels_reversal` (Epic #105 / Issues #107 + #109). Confirmed resistance break + retest in `[level ± retest_zone_atr×ATR]` + close ≥ broken level + bullish trigger. Stop/take = ATR × RR from the pattern params. Not a SignalEngine inline-evaluate id (keep it out of `SIGNAL_ENGINE_PATTERN_IDS`). Schema is in `PATTERN_REGISTRY` (optional `label_en` / `hint_en` / `icon`) and on `GET /api/patterns`. Strategy Lab renders the breakout-group chip and `PatternSettingsModal` fields from that payload — do not hardcode the six params in the frontend. File: `patterns/level_breakout_retest.py` (cannot live under `patterns/breakout/` — that would shadow `breakout.py` / `BO_BB_Squeeze`). Issue #116: Lab plugin path must pass `htf_bars` or the tracker never leaves `active` (zero breakout trades).
- `signal_4h_buy` — 4h BUY aggregate from `trading.signals` (TF fixed; not refactored).
- `rsi_oversold` / `macd_bullish` / `bb_lower` — 1min indicator AND-filters. `rsi_oversold` is not `MR_RSI_Reversal`.
- The ten SignalEngine ids above — AND-filters on the last closed HTF bar via inline `BasePattern.evaluate` on `trading.indicators`. Schemas come from `GET /api/patterns` (`timeframe` select 30min/1h/2h/4h/1d/1w, default 4h, plus 4h numeric defaults). Timeframe is set in the pattern settings modal. `StrategyLab.tsx` groups chips by API `category` (RU titles: levels / signal / trend / price_action / volume / mean_reversion / breakout). It does not hardcode the ten SignalEngine ids or the `level_breakout_retest` param list; the two-chip fallback is only used when `GET /api/patterns` is empty.

## 7. Known Issues & Status

- **Active paper strategy**: `test_20260731` (id=36 in `trading.strategies`, `in_paper_test=true`, `locked=true`). Config: levels_reversal (4h, swing+impulse, window 10, body 0.7, impulse 1.5, zone 0.5) + signal_4h_buy, confirm [10], RR 1:2, commission 0.06%. Universe: 28 tickers from run_params. Verified: 72 signals emitted, 62 positions opened, first closed trade PnL +0.77%. Previous validated strategy `levels_reversal_4hbuy` remains in trading_config.py as reference. Locked DB row is not rewritten by Issue #97.
- **Issue #97 (ALRS paper #711, 2026-08-20)**: `levels_reversal` printed a support entry at 19.80 while price sat in impulse resistance 19.67 [19.40, 19.94]. `nearest_level_at(..., 'support')` is one-sided; the 0.5×ATR support extension then passed the fill. Guard: `overlapping_resistance_zone_at` vetoes `StrategyEvaluator.check_entry`. Case write-up: `docs/strategy/levels-reversal-strategy.md` §10. Test: `tests/test_resistance_zone_veto.py`.
- **Issue #100 (`test_20260820`, 2026-08-21)**: unlocked swing-only Lab config id=102 after the #97 veto. Two published packages, neither locks/paper-flags the row: (1) portfolio replay Issue #44 (`analytics/issue-100-test-20260820-portfolio/`) — equity 87,033.31 RUB, PF 1.37, 1721 trades; (2) Lab full-sample + walk-forward on `get_big_tickers` (`analytics/issue-100-test-20260820-resistance-veto/`) — 28 tickers, median PF 1.52, 26/28 PF>1, 2556 trades, WF avg PF 1.91. ALRS 2026-08-20 11:50:24 @ 19.80 is absent from both trade lists. Do not mix with locked `test_20260731`.
- **Issue #103 (`test_20260821`, 2026-08-21)**: unlocked Lab config id=118 after the #97 veto, `level_method=['swing','impulse']` (same methods as locked `test_20260731`, current `StrategyEvaluator`). Published package `analytics/issue-103-test-20260821-portfolio/` — equity 89,055.31 RUB, PF 1.34, 2070 trades, daily Max DD 6.82%, no GAME OVER. ALRS 2026-08-20 11:50:24 @ 19.80 is absent from candidate and portfolio entries. Do not lock/rename/overwrite `test_20260821`, `test_20260820`, or locked `test_20260731`. This is not a Lab full-sample table and not an ATR comparison.
- **Issue #106 (Epic #105, 2026-08-21)**: in-memory `LevelsTracker` in `levels_engine.py` tracks `active → broken_up/down → flipped_support/resistance`. Breakout thresholds live in `LEVEL_STATE_MACHINE` (`trading_config.py`). `overlapping_resistance_zone_at` skips non-`active` rows when a `state` column is present. No DB persistence. Tests: `tests/test_levels_state_machine.py`.
- **Issue #107 (Epic #105, 2026-08-21)**: Lab pattern `level_breakout_retest` is an AND-filter in `StrategyEvaluator` after `levels_reversal`. Tracker is created and passed into the veto (`is_broken`) only when the pattern is in `config.patterns`. Stop/take then come from `stop_atr` × ATR and pattern `risk_reward`. Locked `test_20260731` does not enable the pattern, so the Issue #97 veto and levels stop/take stay bit-for-bit. Tests: `tests/test_level_breakout_retest.py` plus existing `tests/test_resistance_zone_veto.py` / `tests/test_levels_state_machine.py`.
- **Issue #109 (Epic #105, 2026-08-21)**: Strategy Lab chip + schema-driven `PatternSettingsModal` for `level_breakout_retest`. Names, hints, icon (`breakout_up`), and the six params come from `GET /api/patterns`. Validation uses schema `min`/`max` (blocks Apply and Save+Run). Locked `test_20260731` stays read-only. Next: analytics validation (#3) and optional chart preview (#5 / Epic #87).
- **Issue #116 (Epic #115, 2026-08-29)**: Lab `_run_job` uses the portfolio plugin whenever `config.strategy_name` is set (Lab always writes `levels_reversal`). `_backtest_ticker_plugin` now puts `build_strategy_context()['htf_bars']` on `MarketContext.htf_bars` so `LevelsTracker` sees closed 4h bars. `backtest_results` INSERT goes through `_json_safe` (`pf: Infinity` → `null`). Locked `test_20260731` unchanged (no breakout chip). Tests: `tests/test_strategy_plugin.py`, `tests/test_level_breakout_retest.py`. Unblocks Lab smoke for `#115` / `#119`.
- **Legacy pattern-matrix backtest**: rule-based strategies NOT profitable after commission on MOEX top-3 over 2 years (all PF < 1). Superseded by the levels approach.
- **Universe**: top-15 by PF (`trading_universe`) remains the paper/data-refresh universe via `get_trading_universe()`. Sandbox live execution uses Issue #66 top-5 `LIVE_UNIVERSE` = SBER, LKOH, RUAL, NVTK, GAZP via `get_live_trading_universe()`. Paper `paper_positions` was empty at the #66 snapshot (equity flat at 100,000 RUB), so the live list is backtest + liquidity + ATR, not forward PnL.
- **Sandbox canary (Issue #74, 2026-08-19)**: `LiveExecutor` initialized the top-5 on locked strategy `test_20260731` and submitted a sandbox market BUY on RUAL (37 lots at 26.73, take 28.02, stop 26.19). The next signal for the same ticker was skipped with `reason=duplicate_ticker`. Paper equity kept updating during the session. The runbook lives in handover §19.
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
| P | Live Trading Infrastructure (sandbox execution, market filters, risk controls, alerting, control panel) | Backend execution #59-#62, Telegram #64, monitoring panel #65, live-universe #66, skip-reason logging #73, and first sandbox canary #74 done |
| Q | SignalEngine patterns in Strategy Lab (Epic #78) | #79–#82 done (evaluator, registry schemas, E2E/docs, Lab UI grouping) |
| R | Pattern chart preview in Lab + Signals (Epic #87) | #88 preview API + levels overlays done; #89–#92 pending |
| S | Level Breakout & Role Reversal (Epic #105) | #106 LevelsTracker + #107 `level_breakout_retest` AND-filter + #109 Lab chip done; analytics validation and optional preview pending |
| T | Composite S/R pattern (Epic #115) | #116 Lab/plugin HTF + JSONB Infinity done; #117 `levels_sr_breakout`, #118 Lab chip, #119 AFKS smoke pending |

## 9. Important Notes

- **Sandbox mode**: no real trading. T-Bank API sandbox tokens.
- **Secrets**: .env (`TINVEST_TOKEN` / `TINVEST_ACC` for market data, `TINVEST_SANDBOX` / optional `TINVEST_SANDBOX_ACC` for sandbox execution, `TGM_TOKEN` / `TGM_CHAT` for Telegram, `PSTGRS_PWD`). Never log secrets or reuse market-data credentials for trading.
- **Docker**: rebuild backend image after code changes (`docker compose up -d --build backend`). Backend mounts `./reports` (for last_run.json).
- **Single source of truth**: trading universe + strategy definitions live in `trading_config.py` / `trading.trading_universe`. Do not hardcode ticker lists or strategy params in modules.
- **Locked strategy**: the strategy under paper test has `locked=true`; the API rejects overwriting it (409). Unlock only after the test period.
- **Logging**: DBManager logs to stdout by default. Reroute to stderr in scripts that parse JSON from stdout. Background processes (start_processes.sh) use `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` for unbuffered logging to log files.

## 10. T-Bank Sandbox API Integration

`backend/app/broker/tinkoff_sandbox.py` is the execution boundary for Epic #58. `TinkoffSandboxClient` connects to the dedicated `INVEST_GRPC_API_SANDBOX` endpoint and uses only `client.sandbox`; it never calls the production `orders` service. It provides:
- `execute_order` for market and limit orders (quantity is in lots);
- `check_balance` for free cash by currency;
- `get_positions` for non-zero open portfolio positions;
- `cancel_order` for active sandbox orders.

Operational policy is centralized in `analytics/trading_config.py` (`SANDBOX_TRADING`): sandbox enablement, hard prohibition of real trading, initial capital reference, default currency, retry count/backoff, and account discovery. Secrets are not stored there: dedicated `TINVEST_SANDBOX` / `TINVEST_SANDBOX_ACC` credentials are loaded through `core/config_manager.py`; `TINVEST_TOKEN` / `TINVEST_ACC` are reserved for market data.

Transient gRPC failures (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`) use exponential backoff. Order retries reuse the same idempotency `order_id`, preventing duplicate execution after an uncertain response. If `TINVEST_SANDBOX_ACC` is empty or invalid (`50004`), the client falls back to the first open sandbox account and caches its id; it does not create or fund accounts automatically.

Live verification on 2026-08-16: an operator opened a sandbox account and funded it with 50,000 RUB. `TinkoffSandboxClient` successfully read the balance and positions, submitted a one-lot SBER limit order, and cancelled that order.

## 11. Real-time Order-book Imbalance

`backend/app/analytics/orderbook_imbalance.py` is the shared calculator and mandatory live-entry filter for Issue #60. On every streamed order-book update, `online_data.py` sums quantities over the configured first 10 bid and ask levels and persists:

`volume_imbalance = bid_depth / ask_depth`

Infrastructure defaults live in `trading_config.py` (`ORDERBOOK_IMBALANCE`): depth 10, maximum aggregate age 5 minutes, and default threshold 1.0. The active strategy may override only the top-level `imbalance_threshold`; a live entry passes when its finite imbalance is strictly above that threshold.

`live_engine.py` recalculates the ratio from `bid_depth` and `ask_depth` in the latest fresh `trading.online_orderbook_aggregates` row before emitting every signal. Missing/stale rows, null/non-finite values, and zero ask depth all produce `None`, so the mandatory filter rejects the signal rather than silently using zero or stale data. The legacy online signal path uses the same calculator.

## 12. Position Sizing

`backend/app/analytics/position_sizer.py` provides the shared `calculate_position_size()` function for live order sizing. It first calculates the capital budget implied by the configured per-trade risk and stop distance, then caps that budget by the maximum allowed portfolio concentration:

`size_rub = min(capital_rub * risk_per_trade_pct / stop_distance_pct, capital_rub * max_position_pct / 100)`

The executable quantity is the whole number of instrument lots that fit the budget: `floor(size_rub / (price * lot_size))`. If the budget is below one lot but free capital can still pay for one lot, the result is raised to one lot with reason `min_lot`. A non-positive stop distance returns `invalid_stop`; capital below one full lot returns `insufficient_capital`.

Default limits are centralized in `trading_config.py` (`POSITION_SIZING`): 1% risk per trade and 20% maximum position concentration. The result also reports whether risk, concentration, or minimum-lot handling determined the final size.

## 13. Sandbox Live Executor

`backend/app/analytics/live_executor.py` implements `LiveExecutor` without changing `StrategyEvaluator`. On initialize it intersects the locked paper-strategy tickers with `get_live_trading_universe()` (Issue #66: SBER, LKOH, RUAL, NVTK, GAZP) so sandbox orders stay on names that have a live order book. Per ticker it loads the active locked strategy and 4h context, feeds the latest closed row from `online_candles_1min` into `check_entry`, and applies the mandatory fresh imbalance filter before any broker call. A passing BUY checks free RUB cash, sizes through `calculate_position_size`, submits a sandbox market order, and persists broker IDs and lifecycle state in `trading.live_positions`. Each rejected BUY is logged as one structured line with ticker, a stable `reason` code, and the relevant numbers; silence in `executor.log` means `StrategyEvaluator` produced no BUY, not a dead filter. Read-only preflight lives in `live_executor_preflight.py`; the first-session runbook is handover §19.

The take-profit is submitted immediately as a resting sell-limit. The stop-loss is intentionally synthetic: a sell-limit below the current market would execute immediately, so the executor waits until the broker's current price reaches the stop, cancels the take, and then submits the stop sell-limit at the observed price. Position reconciliation polls `get_positions()`; disappearance after a take or triggered stop closes the DB row and calculates PnL. External SELL handling cancels protection and closes through a sandbox market order.

Every broker API attempt, including internal retries and account discovery, passes through a token bucket capped at 10 requests/second. SIGTERM/SIGINT only sets a shutdown flag; final cleanup then cancels pending entry/protection orders, updates DB state, optionally flattens open sandbox positions when `close_positions_on_shutdown` is enabled, and closes the standalone DB pool. The complete policy is returned by `get_live_trading_config()` from `trading_config.py`.

## 14. Telegram Alerting

`backend/app/notifications/telegram_notifier.py` sends Markdown messages through the Telegram Bot API. It uses `TGM_TOKEN` and `TGM_CHAT`; legacy `TGM_CHAT_ID` remains a fallback. `TGM_APP_ID` and `TGM_APP_HASH` are loaded for configuration compatibility but are not required by the Bot API.

Delivery is serialized and limited to one attempt per second. Network/API errors are logged and returned as `False`, never propagated into the trading loop. `paper_trader.py` emits alerts after successful DB writes for market and limit opens and for every close (including stop/take). Equity updates emit a critical alert only when drawdown first crosses `risk.max_daily_loss_pct`, or when equity first reaches zero (GAME OVER), preventing repeated alerts on every loop.

## 15. Live Trading Monitoring Panel

`frontend/src/components/LiveTradingPanel.tsx` is available from the `Live Trading` tab. It polls `trading.live_positions` through the live monitoring API every 10 seconds and shows open positions with the latest best bid (best ask fallback), unrealized RUB/% PnL, paginated and sortable trade history, cumulative realized PnL, and Telegram connectivity. Both tables use the shared `ui/DataTable` and `FilterChips`; date filters use the shared `ui/DatePicker` extracted from Strategy Lab.

`/api/live-trading/positions` and `/api/live-trading/dynamics` keep sandbox execution data separate from paper trading. They support ticker/date/status filters; the special `status=closed` value selects both stop and take closures. `/api/notifications/status` performs a read-only Telegram `getMe` probe and caches the result for 30 seconds. It never returns credentials.

## 16. SignalEngine AND-filters in StrategyEvaluator

Issue #79 connects the ten Signals-tab `BasePattern` classes to `StrategyEvaluator` as AND-filters after `levels_reversal` (stop/take stay levels-only). The path is fixed and must not be mixed:

- `signal_4h_buy` continues to look up `trading.signals` (4h BUY aggregate). It is not refactored.
- SignalEngine ids are evaluated **inline** with `SignalEngine.process_dataframe` / `BasePattern.evaluate` on `trading.indicators` for the selected HTF. They never look up `trading.signals` by `pattern_name`.
- `rsi_oversold` remains the 1min RSI<30 filter and is not a substitute for `MR_RSI_Reversal`.

`timeframe` is a select like `level_timeframe`. Supported values match SignalEngine thresholds: 30min, 1h, 2h, 4h, 1d, 1w (default 4h). Full Lab schemas live in `SIGNAL_ENGINE_PATTERN_SCHEMAS` / `PATTERN_REGISTRY` (`pattern_registry.py`); numeric defaults are the 4h `get_thresholds` (or `evaluate` literals for PA). `normalize_patterns` stores those params, while `StrategyEvaluator` currently keys inline evaluate by `timeframe` only. The filter uses the last *closed* HTF bar (bar open + TF delta <= current 1min ts) so backtests do not look ahead into a still-forming bucket. Missing HTF indicator rows reject the entry. `2h` is in the contract because patterns define thresholds for it, but the current candle/indicator pipeline does not persist 2h, so that selection currently yields no trades.

`build_strategy_context` precomputes BUY timestamps per enabled filter and passes `signal_filter_series` into `StrategyEvaluator.load_context` (backtest, paper, live). Default `levels_reversal` + `signal_4h_buy` (including locked `test_20260731`) does not enable any SignalEngine id, so trade lists stay unchanged. E2E coverage: `tests/test_signal_pattern_e2e.py` (`levels_reversal` + one registry id such as `PA_Engulfing` on 4h).
