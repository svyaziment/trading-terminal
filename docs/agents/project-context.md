# Project Context: Trading Terminal

Last refreshed: 2026-09-08 (Product Owner decision recorded in §18: `ultra_late_tight` — the densest late ladder of the `143-trailing-v3` lattice — is the production default trailing-stop grid for #144; epic #142 / roadmap Block W; the #144 contract itself landed the same day — validation only, full field contract in section 6, tests `backend/tests/test_trailing_contract.py`). Source: docs/refresh/context_collector.py + git ls-files.
This file is the canonical project context for agents. Keep it current.

## 1. Project Overview

Trading terminal for MOEX stocks. Sandbox mode (no real trading). Stack: FastAPI backend (Python 3.12), React frontend (Vite + Tailwind + lightweight-charts), PostgreSQL (external, via host.docker.internal from Docker). Market data: T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1min candles).

Three pillars:
1. **Backtest / Strategy Lab** - parameterizable strategy engine (AND-patterns, multi-window confirmation, commission/slippage/RR, depth presets, bootstrap) + walk-forward validation, exposed via API and a constructor UI.
2. **Paper trading** - the active strategy from Strategy Lab (table `strategies`, `in_paper_test=true AND locked=true`) trades virtually via the unified `StrategyEvaluator` (single brain with backtest). Current: `test_20260830_new_level` (levels_sr_support + signal_4h_buy, RR 1:3, confirm 10min, 28 Lab tickers). Previous locked row `test_20260731` is unlocked and kept as reference. Single arm: market entry, window mode (7-19 MSK), RR from config.

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
│ │ │ ├── trading_config.py # SINGLE SOURCE OF TRUTH: universe, LIVE_UNIVERSE (12-name PO list; #66 top-5 is historical), strategies, live risk policy, LEVEL_STATE_MACHINE (#106), LEVEL_BREAKOUT_RETEST (#107); tracker also for levels_sr_breakout (#117) and levels_sr_support (#127)
│ │ │ ├── position_sizer.py # Hybrid risk/concentration sizing + lot rounding
│ │ │ ├── live_executor.py # Sandbox execution, protection, reconciliation, shutdown
│ │ │ ├── moex_session.py # MOEX 10:00-19:00 MSK calendar for overnight LiveExecutor (Issue #137)
│ │ │ ├── moex_session.py # MOEX 10:00–19:00 MSK calendar for overnight LiveExecutor (#137)
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
│ │ │ └── patterns/ # 10 SignalEngine modules + Lab level_breakout_retest.py (#107) + levels_sr_breakout.py (#117) + levels_sr_support.py (#127; not under breakout/)
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
│       ├── test_levels_sr_breakout.py # Issue #117 composite OR paths / source / engine guard
│       ├── test_levels_sr_support.py # Issue #127 support-only + tracker veto / source / engine guard
│       ├── test_issue100_analysis.py # Issue #100 Lab universe/veto/baseline helpers
│       ├── test_issue119_analysis.py # Issue #119 AFKS smoke config/source/verdict helpers
│       ├── test_issue124_analysis.py # Issue #124 Lab-universe A/B / AFKS / ALRS / verdict helpers
│       ├── test_issue129_analysis.py # Issue #129 isolated support universe vs #124 B-support
│       ├── test_issue130_analysis.py # Issue #130 50k portfolio of levels_sr_support vs #44/#103
│       └── test_portfolio_simulator.py # Portfolio simulator unit + integration tests
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Main app (tabs: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Signals table (sort, filter, pagination)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: chips from GET /api/patterns (#82/#109/#118/#128)
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (filters, PnL chart, positions)
│ │ │ ├── LiveTradingPanel.tsx # Live monitoring: open/history/equity/Telegram
│   │   ├── PatternSettingsModal.tsx # Schema-driven pattern settings modal (Epic #11; min/max errors #109)
│   │   ├── PatternIcon.tsx          # Lab chip icons from API `icon` (breakout_up, support_breakout, support_tracker)
│ │ │ ├── PipelineWidget.tsx # Refresh/regenerate status widget
│ │ │ ├── CandleChart.tsx # Candlestick chart
│ │ │ ├── InstrumentsPanel.tsx # Instruments list
│ │ │ ├── PatternStatsPanel.tsx # Pattern statistics
│ │ │ ├── SignalDetailModal.tsx # Signal detail modal
│ │ │ └── TopStocksPanel.tsx # Top stocks by volume
│ │ ├── api.ts # API client
│ │ ├── types.ts # TypeScript types (incl. LevelBreakoutRetestConfig, LevelsSrBreakoutConfig, LevelsSrSupportConfig)
│ │ ├── patternLab.ts # Chip grouping + RU/EN labels + confirm_windows priority (#82/#109/#128)
│ │ ├── patternValidation.ts # Schema min/max checks before Lab save/run (#109)
│ │ └── index.css / main.tsx
│ └── package.json / tailwind.config.js / vite.config.js
├── analytics/ # Git-tracked, published analytical results
│ ├── issue-44-strategy-comparison/ # Notebook, report, metrics, and plots
│ ├── issue-66-live-universe/ # Live top-5 ranking, report, and plots
│ ├── issue-100-test-20260820-portfolio/ # Portfolio replay of Lab test_20260820 after #97 veto
│ ├── issue-100-test-20260820-resistance-veto/ # Lab full-sample + walk-forward of test_20260820 after #97 veto
│ ├── issue-103-test-20260821-portfolio/ # Portfolio replay of Lab test_20260821 after #97 veto (swing+impulse)
│ ├── issue-119-afks-sr-breakout-smoke/ # Isolated AFKS A/B smoke for levels_sr_breakout (#119)
│ ├── issue-124-sr-breakout-universe/ # Isolated Lab-universe A/B for levels_sr_breakout (#124)
│ ├── issue-129-sr-support-universe/ # Isolated Lab-universe C vs #124 B-support (#129)
│ └── issue-130-sr-support-portfolio/ # 50k slot replay of levels_sr_support (#130)
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
- `strategies`: id, name (unique), config (jsonb: patterns, confirm_windows, commission_pct, slippage_pct, risk_reward, trailing_stop, n_runs), in_paper_test (bool), locked (bool), description. `trailing_stop` is the top-level stepped-trailing exit block of Issue #144 (§6, no schema migration, default OFF) — the other keys keep their pre-#144 meaning.
- `backtest_results`: id, strategy_id (FK), ticker, test_type (full_sample/walkforward), depth, metrics (jsonb), created_at
- `paper_positions`: id, ticker, entry_ts/price, stop_price, take_price, limit_price, limit_ts, size_lots, size_rub, lot_size, status (pending/open/closed_stop/closed_take/cancelled), signal_source, window_mode, rr_mode, rr_ratio, entry_mode (market/limit), signal_id, strategy_name, exit_ts/price/reason, pnl_rub, pnl_pct
- `live_positions`: id, ticker, instrument_id, signal_ts, entry_price, lot_size, size_lots, stop_price, take_price, broker_order_id/stop_id/take_id, status, strategy_name, exit_ts/price/reason, pnl_rub
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 4. Data Pipeline

**Historical / refresh** (`data_refresher.py`, background, every 15 min, `get_streaming_universe()` = top-15 ∪ LIVE_UNIVERSE):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Keeps 4h BUY signals fresh for the base_4hbuy arm.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
- paper_trader: reads strategy config from DB (RR from `config.risk_reward`), alerts -> market positions (open at best_ask, single arm) -> monitor stop/take -> write equity. Records `strategy_name` in `paper_positions` and sends best-effort Telegram alerts for opens, closes, stop/take, drawdown threshold crossings, and GAME OVER.
- On startup `start_processes.sh` runs `position_catchup.py` (resolve pending + check open against historical 1min candles).

**Sandbox live execution** (`live_executor.py`, opt-in background process): uses the same `StrategyEvaluator` and live 1min context, then requires a MOEX entry window [10:00, 19:00) MSK, fresh order-book imbalance, checks sandbox cash, calculates whole-lot size, submits a market BUY, and records the position in `trading.live_positions`. Overnight start: `START_LIVE_EXECUTOR=1 ./start_processes.sh` (no `DURATION_MINUTES`) waits until 10:00 MSK, enters only until 19:00, and keeps stop/take until the position closes by price. Normal paper startup does not place broker orders.

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
- `levels_reversal` — required for the classic support path; 4h support zone + confirmation; defines stop/take. Issue #97: `check_entry` rejects the bar when the 1min close sits in an active resistance zone (`overlapping_resistance_zone_at`); this is a defect, not role-reversal. Issue #106: when a `state` column is present, the veto skips non-`active` zones. Issue #107: `StrategyEvaluator` passes `LevelsTracker` into the veto when `level_breakout_retest`, `levels_sr_breakout`, or `levels_sr_support` is enabled; `build_levels` still has no `state`, so locked `test_20260731` stays bit-for-bit. Not required in `config.patterns` when `levels_sr_breakout` or `levels_sr_support` is the entry engine.
- `levels_sr_support` — isolated Lab entry engine (Epic #126 / Issue #127): **only** the #124 B-support path. Same support geometry as `levels_reversal` (zone + 0.5×ATR extension + confirm + levels stop/take + top-level RR) plus the Issue #97 veto of *active* resistance **with** `LevelsTracker` (`source=levels_sr_support`). Does **not** call `check_breakout_retest`. Isolated run: `config.patterns` has `levels_sr_support` (optionally `signal_4h_buy`) **without** `levels_reversal` / `levels_sr_breakout` / `level_breakout_retest`. If `levels_sr_breakout` is also on, the composite wins. If `levels_sr_support` and `levels_reversal` are both on, the new id wins (one support path). Not a SignalEngine id. Category `levels`, icon `support_tracker` (distinct from `breakout_up` and `support_breakout`). Schema = `levels_reversal` fields only — no retest keys; `normalize_patterns` fills defaults. File: `patterns/levels_sr_support.py` (not under `patterns/breakout/`). Lab chip is in the **Уровни** group from `GET /api/patterns`; `PatternSettingsModal` and validation stay schema-driven — do not hardcode the param list in TSX. Keep it off on locked `test_20260731`.
- `levels_sr_breakout` — isolated Lab entry engine (Epic #115 / Issues #117 + #118), OR of two paths. Path A = support geometry of `levels_reversal` + veto of *active* resistance (`source=levels_sr_breakout_support`). Path B = `check_breakout_retest` without a native support zone (`source=levels_sr_breakout_resistance`, ATR × RR stop/take; top-level config RR is not applied again). Common AND: session, HTF, optional `signal_4h_buy` / SignalEngine. If both paths fire on the same bar, path B wins. If both `levels_reversal` and `levels_sr_breakout` are in `config.patterns`, the composite wins (one support path, no doubling). Do not AND with `level_breakout_retest` as a substitute. Not a SignalEngine id. Category `levels`, icon `support_breakout` (distinct from `breakout_up`). Schema = all `levels_reversal` fields + retest fields; `normalize_patterns` fills defaults. File: `patterns/levels_sr_breakout.py` (not under `patterns/breakout/`). Lab chip is in the **Уровни** group from `GET /api/patterns`; `PatternSettingsModal` and validation stay schema-driven — do not hardcode the param list in TSX. This chip replaces `levels_reversal` for the new strategy; keep it off on locked `test_20260731`.
- `level_breakout_retest` — Lab AND-filter after `levels_reversal` (Epic #105 / Issues #107 + #109). Confirmed resistance break + retest in `[level ± retest_zone_atr×ATR]` + close ≥ broken level + bullish trigger. Stop/take = ATR × RR from the pattern params. Not a SignalEngine inline-evaluate id (keep it out of `SIGNAL_ENGINE_PATTERN_IDS`). Schema is in `PATTERN_REGISTRY` (optional `label_en` / `hint_en` / `icon`) and on `GET /api/patterns`. Strategy Lab renders the breakout-group chip and `PatternSettingsModal` fields from that payload — do not hardcode the six params in the frontend. File: `patterns/level_breakout_retest.py` (cannot live under `patterns/breakout/` — that would shadow `breakout.py` / `BO_BB_Squeeze`). Issue #116: Lab plugin path must pass `htf_bars` or the tracker never leaves `active` (zero breakout trades). Different contract from `levels_sr_breakout` — do not replace this AND-filter with the composite.
- `signal_4h_buy` — 4h BUY aggregate from `trading.signals` (TF fixed; not refactored).
- `rsi_oversold` / `macd_bullish` / `bb_lower` — 1min indicator AND-filters. `rsi_oversold` is not `MR_RSI_Reversal`.
- The ten SignalEngine ids above — AND-filters on the last closed HTF bar via inline `BasePattern.evaluate` on `trading.indicators`. Schemas come from `GET /api/patterns` (`timeframe` select 30min/1h/2h/4h/1d/1w, default 4h, plus 4h numeric defaults). Timeframe is set in the pattern settings modal. `StrategyLab.tsx` groups chips by API `category` (RU titles: levels / signal / trend / price_action / volume / mean_reversion / breakout). It does not hardcode the ten SignalEngine ids or the `level_breakout_retest` / `levels_sr_breakout` / `levels_sr_support` param lists; the two-chip fallback is only used when `GET /api/patterns` is empty.
- **Trailing-stop exit contract (Issue #144, Epic #142)**: `config.trailing_stop` is the top-level exit-policy block whose schema, defaults and validation helpers live in `trading_config.py`. Shape: `{"enabled": bool, "steps": [{"trigger": 2.0, "stop": 1.9}, ...]}` in R multiples measured from the entry — there is **no `take_partial`** and no `trigger_r` / `lock_r` naming, because a partial take is not part of the approved grid. `validate_trailing_steps()` never raises: it returns the stable reason codes `trailing_disabled` / `trailing_step_invalid` / `trailing_not_monotonic` / `trailing_too_many_steps` (empty list = accepted), `normalize_trailing_stop()` canonicalizes, and `resolve_trailing_stop(config)` hands every consumer `{'enabled', 'steps', 'reasons'}`. Bounds come from `TRAILING_STOP` and are the single source of truth: `0 < trigger <= 3.5`, `0 <= stop <= 3.0`, always `stop < trigger`, at most `max_steps` = 6 steps, and a step must be a dict (a list of pairs is not accepted). There is deliberately **no minimum trigger–stop gap** — the shipped ladder lives on 0.1R, so a "gap ≥ 0.5R" rule would reject the default. Default policy `TRAILING_STOP` = `enabled=false`, steps `2.0→1.9 / 2.5→2.4 / 3.0→2.9` — the `ultra_late_tight` grid the Product Owner approved on 2026-09-08 (handover §35, §18) and shipped **disabled**, so no config changes behaviour. An absent key, `None`, `{}` or a non-dict block mean "no trailing" with an empty reasons list; `enabled=true` with no usable steps reports `trailing_disabled`. `enabled` is strict (only a real bool or `'1' / 'true' / 'yes' / 'on'` arms the ladder) and normalization keeps float precision — 1.9 / 2.4 / 2.9 are never rounded to 0.5R. **Contract only, no write-path gate:** `validate_config()` does not exist in this repository, so nothing calls these helpers yet and a malformed ladder still saves; `require_valid_trailing_stop()` is the gate #146 / #149 are expected to call. `StrategyEvaluator.on_bar`, the plugin path, the portfolio simulator, paper and live still ignore the block (#145, #148, #151), and `EXIT_TRAILING` is declared in `backtest_models.py` without ever being emitted. The legacy pattern-level `trailing_stop` / `trailing_step` params in `pattern_registry.py` are a different contract untouched by #144. Test: `backend/tests/test_trailing_contract.py` (33).

## 7. Known Issues & Status

- **Active paper strategy**: `test_20260830_new_level` (id=126 in `trading.strategies`, `in_paper_test=true`, `locked=true`). Config: `levels_sr_support` + `signal_4h_buy` (4h, swing+impulse, window 10, body 0.7, impulse 1.5, zone 0.5), confirm [10], RR 1:3, commission 0.06%. Lab universe: 28 tickers from `run_params`. Previous locked row `test_20260731` (id=36) is unlocked and kept as reference; its config is not rewritten. PO override of the #130 «not paper» verdict for published C (different RR 1:2 book).
- **Issue #97 (ALRS paper #711, 2026-08-20)**: `levels_reversal` printed a support entry at 19.80 while price sat in impulse resistance 19.67 [19.40, 19.94]. `nearest_level_at(..., 'support')` is one-sided; the 0.5×ATR support extension then passed the fill. Guard: `overlapping_resistance_zone_at` vetoes `StrategyEvaluator.check_entry`. Case write-up: `docs/strategy/levels-reversal-strategy.md` §10. Test: `tests/test_resistance_zone_veto.py`.
- **Issue #100 (`test_20260820`, 2026-08-21)**: unlocked swing-only Lab config id=102 after the #97 veto. Two published packages, neither locks/paper-flags the row: (1) portfolio replay Issue #44 (`analytics/issue-100-test-20260820-portfolio/`) — equity 87,033.31 RUB, PF 1.37, 1721 trades; (2) Lab full-sample + walk-forward on `get_big_tickers` (`analytics/issue-100-test-20260820-resistance-veto/`) — 28 tickers, median PF 1.52, 26/28 PF>1, 2556 trades, WF avg PF 1.91. ALRS 2026-08-20 11:50:24 @ 19.80 is absent from both trade lists. Do not mix with locked `test_20260731`.
- **Issue #103 (`test_20260821`, 2026-08-21)**: unlocked Lab config id=118 after the #97 veto, `level_method=['swing','impulse']` (same methods as locked `test_20260731`, current `StrategyEvaluator`). Published package `analytics/issue-103-test-20260821-portfolio/` — equity 89,055.31 RUB, PF 1.34, 2070 trades, daily Max DD 6.82%, no GAME OVER. ALRS 2026-08-20 11:50:24 @ 19.80 is absent from candidate and portfolio entries. Do not lock/rename/overwrite `test_20260821`, `test_20260820`, or locked `test_20260731`. This is not a Lab full-sample table and not an ATR comparison.
- **Issue #106 (Epic #105, 2026-08-21)**: in-memory `LevelsTracker` in `levels_engine.py` tracks `active → broken_up/down → flipped_support/resistance`. Breakout thresholds live in `LEVEL_STATE_MACHINE` (`trading_config.py`). `overlapping_resistance_zone_at` skips non-`active` rows when a `state` column is present. No DB persistence. Tests: `tests/test_levels_state_machine.py`.
- **Issue #107 (Epic #105, 2026-08-21)**: Lab pattern `level_breakout_retest` is an AND-filter in `StrategyEvaluator` after `levels_reversal`. Tracker is created and passed into the veto (`is_broken`) only when the pattern is in `config.patterns`. Stop/take then come from `stop_atr` × ATR and pattern `risk_reward`. Locked `test_20260731` does not enable the pattern, so the Issue #97 veto and levels stop/take stay bit-for-bit. Tests: `tests/test_level_breakout_retest.py` plus existing `tests/test_resistance_zone_veto.py` / `tests/test_levels_state_machine.py`.
- **Issue #109 (Epic #105, 2026-08-21)**: Strategy Lab chip + schema-driven `PatternSettingsModal` for `level_breakout_retest`. Names, hints, icon (`breakout_up`), and the six params come from `GET /api/patterns`. Validation uses schema `min`/`max` (blocks Apply and Save+Run). Locked `test_20260731` stays read-only. Next: analytics validation (#3) and optional chart preview (#5 / Epic #87).
- **Issue #116 (Epic #115, 2026-08-29)**: Lab `_run_job` uses the portfolio plugin whenever `config.strategy_name` is set (Lab always writes `levels_reversal`). `_backtest_ticker_plugin` now puts `build_strategy_context()['htf_bars']` on `MarketContext.htf_bars` so `LevelsTracker` sees closed 4h bars. `backtest_results` INSERT goes through `_json_safe` (`pf: Infinity` → `null`). Locked `test_20260731` unchanged (no breakout chip). Tests: `tests/test_strategy_plugin.py`, `tests/test_level_breakout_retest.py`. Unblocks Lab smoke for `#115` / `#119`.
- **Issue #117 (Epic #115, 2026-08-29)**: Lab pattern `levels_sr_breakout` is an isolated entry engine (OR of support path A and resistance-break path B). `run_strategy_backtest` accepts it without `levels_reversal` in `config.patterns`. Tracker + `htf_bars` as in #107/#116. Locked `test_20260731` unchanged. Tests: `tests/test_levels_sr_breakout.py` plus existing veto / breakout-retest / plugin.
- **Issue #118 (Epic #115, 2026-08-29)**: Strategy Lab chip + schema-driven `PatternSettingsModal` for `levels_sr_breakout`. Names, hints, icon (`support_breakout`), and params come from `GET /api/patterns` (group **Уровни**). Validation uses schema `min`/`max` (blocks Apply and Save+Run). `level_breakout_retest` stays in **Пробой**. Locked `test_20260731` stays read-only.
- **Issue #119 (Epic #115, 2026-08-29)**: Isolated AFKS smoke for `levels_sr_breakout` vs #44/#103. Package: `analytics/issue-119-afks-sr-breakout-smoke/`. Period `2024-08-01` … `timestamp < 2026-08-21`. A (`levels_reversal` + `signal_4h_buy`): n=39, PF 1.50. B (`levels_sr_breakout` + `signal_4h_buy`): n=116, PF 1.46; path A `source=levels_sr_breakout_support` n=78 PF 1.70; path B `source=levels_sr_breakout_resistance` n=38 PF 1.20. B-support > A because the composite passes `LevelsTracker` into the veto. Plugin path n/PF match; plugin trades still omit `source`. Locked `test_20260731` / `test_20260820` / `test_20260821` untouched. Verdict: expand the universe (not paper). Not a 50k portfolio replay.
- **Issue #124 (Epic #115, 2026-08-30)**: Isolated Lab-universe A/B for `levels_sr_breakout` vs #103/#119. Package: `analytics/issue-124-sr-breakout-universe/`. Same SHA pair as #119, `get_big_tickers` (28 names), `2024-08-01` … `timestamp < 2026-08-21`. Isolated A: n=2559, PF 1.46, median PF 1.48, 26/28 PF>1. Isolated B: n=4799, PF 1.39, median PF 1.39, 28/28 PF>1; support n=3811 PF 1.51; resistance n=988 PF 1.17. Extra support vs A: +1252 (tracker in the veto). AFKS matched #119 (39/1.50 and 116/1.46). ALRS `2026-08-20 11:50:24` @ 19.80 absent in A and B. Optional #44/#103 slot replay of B: n=2837, PF 1.32, equity 98,432.94 RUB, no GAME OVER. Locked `test_20260731` / `test_20260820` / `test_20260821` untouched. Verdict: portfolio replay done; not paper.
- **Issue #127 (Epic #126, 2026-08-30)**: Lab pattern `levels_sr_support` is the isolated B-support engine from #124 (tracker veto, no retest). `run_strategy_backtest` accepts it without `levels_reversal` in `config.patterns`. Tracker + `htf_bars` as in #107/#116. Locked `test_20260731` unchanged. Tests: `tests/test_levels_sr_support.py` plus existing veto / composite / plugin. Lab chip is #128; isolated vs #124 B-support is #129.
- **Issue #128 (Epic #126, 2026-08-30)**: Strategy Lab chip + schema-driven `PatternSettingsModal` for `levels_sr_support`. Names, hints, icon (`support_tracker`), and params come from `GET /api/patterns` (group **Уровни**). Validation uses schema `min`/`max` (blocks Apply and Save+Run). No retest fields. `resolveConfirmWindows` follows backend order: `levels_sr_breakout` > `levels_sr_support` > `levels_reversal`. Locked `test_20260731` stays read-only. Isolated vs #124 B-support is #129.
- **Issue #129 (Epic #126, 2026-08-30)**: Isolated Lab-universe backtest of `levels_sr_support` + `signal_4h_buy` vs exclusive #124 B-support. Package: `analytics/issue-129-sr-support-universe/`. Same 28 names / period as #124. Isolated C: n=4380 PF 1.45, median PF 1.48, 26/28 PF>1, `source=levels_sr_support` only (resistance n=0). Exclusive B-support 3811 / 1.51 is a composite label (path B occupies the slot), not the runnable book. Extra 611: occupancy 610 + leftover 1 (PHOR 2026-08-14 14:48); missing 42 cascade. AFKS C 89 / 1.49 with exclusive 78 ⊆ C (not mix 116 / 1.46). ALRS `2026-08-20 11:50:24` @ 19.80 blocked. Locked `test_20260731` / `test_20260820` / `test_20260821` untouched. Verdict: совпало; #130 must use C, not exclusive. Not paper.
- **Issue #130 (Epic #126, 2026-08-30)**: Portfolio replay of isolated C (`levels_sr_support` + `signal_4h_buy`) by Issue #44 slot rules. Package: `analytics/issue-130-sr-support-portfolio/`. Same 28 names / volume-order as #103, `2024-08-01` … `< 2026-08-21`. Candidates = published #129 C (n=4380, SHA `3b7864c4…aedb1b`), not exclusive 3811 / 1.51 and not a `source=` filter of #124 B-mix. Portfolio C: n=3237, PF 1.33, equity 96,204.63 RUB, daily Max DD 6.08%, skipped 1143, no GAME OVER. ALRS `2026-08-20 11:50:24` @ 19.80 absent in candidates and portfolio. Resistance n=0. Comparison: #44 96,343.49 / 3500 / 1.31; #103 89,055.31 / 2070 / 1.34; #124 B-mix 98,432.94 / 2837 / 1.32. Locked `test_20260731` / `test_20260820` / `test_20260821` untouched. Verdict: not paper.
- **Issue #135 (2026-08-30)**: PO paper + sandbox of Lab row `test_20260830_new_level` (id=126, `levels_sr_support` + `signal_4h_buy`, RR 1:3). Unlocks `test_20260731`. `LIVE_UNIVERSE` becomes the nine PO names. Streaming uses top-15 ∪ live-9. Full sandbox day is the next MOEX session (handover §33). Not a rewrite of published C / #130.
- **Issue #137 (2026-08-30)**: Overnight LiveExecutor. Without `DURATION_MINUTES`, `start_processes.sh` sizes paper until the next session open after 19:00; LiveExecutor waits until 10:00, **enters only [10:00, 19:00)**, and keeps stop/take until the price hits (also after 19:00). Calendar: `MOEX_SESSION` / `moex_session.py`. Canary still uses explicit `DURATION_MINUTES=N`.
- **LIVE_UNIVERSE extension (2026-09-02)**: PO added FEES, GAZP, PLZL. Sandbox list is 12 names. Strategy remains `test_20260830_new_level`. Not a rewrite of #66 or published C.
- **Issue #144 (Epic #142, 2026-09-08)**: `config.trailing_stop` contract in `backend/app/analytics/trading_config.py` — `TRAILING_STOP` default block (Product Owner grid `ultra_late_tight` = `2.0→1.9 / 2.5→2.4 / 3.0→2.9`, shipped `enabled=false`) + `normalize_trailing_stop()` / `validate_trailing_steps()` / `resolve_trailing_stop()` / `require_valid_trailing_stop()`. **Contract only:** nothing calls these helpers yet — there is no `validate_config()` in this repo — so a malformed ladder still saves until #146 / #149 gate on `require_valid_trailing_stop()`. JSONB key only, no migration; no engine / paper / live behaviour change (that is #145 / #148 / #151). Tests: `backend/tests/test_trailing_contract.py` (33: acceptance §6 cases + defaults + backward compatibility + reason-code wording + the shape #146 will reuse).
- **Legacy pattern-matrix backtest**: rule-based strategies NOT profitable after commission on MOEX top-3 over 2 years (all PF < 1). Superseded by the levels approach.
- **Universe**: top-15 by PF (`trading_universe`) remains the paper ranking via `get_trading_universe()`. Do not shrink that table. Streaming and data refresh use `get_streaming_universe()` = top-15 ∪ `LIVE_UNIVERSE`. Sandbox live execution uses `LIVE_UNIVERSE` = ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL via `get_live_trading_universe()` (no clip against top-15). The Issue #66 top-5 (SBER, LKOH, RUAL, NVTK, GAZP) stays historical in `analytics/issue-66-live-universe/`.
- **Sandbox canary (Issue #74, 2026-08-19)**: `LiveExecutor` initialized the then top-5 on locked strategy `test_20260731` and submitted a sandbox market BUY on RUAL (37 lots at 26.73, take 28.02, stop 26.19). That RUAL row is now `closed_stop`. The next signal for the same ticker was skipped with `reason=duplicate_ticker`. Historical runbook: handover §19. Current sandbox day: Issue #135 / handover §33.
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
| T | Composite S/R pattern (Epic #115) | #116 Lab/plugin HTF + JSONB Infinity + #117 `levels_sr_breakout` + #118 Lab chip + #119 AFKS smoke + #124 Lab-universe A/B done |
| U | Support with tracker (Epic #126) | #127 `levels_sr_support` backend + #128 Lab chip + #129 isolated Lab universe + #130 portfolio #44 done |
| V | Stepped trailing-stop analytics (Issue #139) | Done — analytics-only A/B (fixed 1:3 vs stepped trailing) on locked `test_20260830_new_level` id=126, RR 1:3; trailing lives in `analytics/.../trailing.py`, not wired into the production exit path |
| W | Stepped trailing stop in production (Epic #142) | In progress — #144 `config.trailing_stop` contract **done** (defaults + validators, shipped OFF, no production caller yet — §6), #145 engine/plugin/portfolio simulator, #146 Lab editor, #147 parity gate vs #139, #143 robustness analytics done (8-grid lattice `143-trailing-v3`, walk-forward, cost stress — §18; the lattice shape itself was re-delivered by #155; **the Product Owner approved the production default grid on 2026-09-08: `ultra_late_tight`**), #148 paper trader, #149 API + filters, #150 Paper/Live panels, #151 sandbox `LiveExecutor`, #152 live-period acceptance verdict. Ships **default OFF**; basis is the #139 result (A 95 180.01 -> B 103 176.00 RUB, PF 1.41 -> 1.54, daily MaxDD 6.49% -> 2.74%) and the #143 lattice (95 827 … 110 434 RUB); the #139 grid `ref139` stays the parity anchor that #147 injects explicitly |


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

`backend/app/analytics/live_executor.py` implements `LiveExecutor` without changing `StrategyEvaluator`. On initialize it intersects the locked paper-strategy tickers with `get_live_trading_universe()` (PO list: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL) so sandbox orders stay on the configured live names. Per ticker it loads the active locked strategy and 4h context, feeds the latest closed row from `online_candles_1min` into `check_entry`, and applies the mandatory fresh imbalance filter before any broker call. A passing BUY checks free RUB cash, sizes through `calculate_position_size`, submits a sandbox market order, and persists broker IDs and lifecycle state in `trading.live_positions`. Each rejected BUY is logged as one structured line with ticker, a stable `reason` code, and the relevant numbers; silence in `executor.log` means `StrategyEvaluator` produced no BUY, not a dead filter. Issue #137: `until_session_end=True` waits for MOEX 10:00 MSK (computer clock → UTC+3) before `initialize()`, refuses new entries outside [10:00, 19:00) with `reason=outside_entry_window`, and keeps stop/take until the position closes by price (also after 19:00). Session bounds live in `MOEX_SESSION` / `moex_session.py`. Read-only preflight lives in `live_executor_preflight.py`; overnight runbook is handover §15 / §33 (historical canary: §19).

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

## 17. Stepped trailing-stop analytics (Issue #139)

`analytics/issue-139-trailing-stop-new-level/` is an analytics-only A/B in the 50k /
10k / max-5 / volume-priority / GAME OVER portfolio simulator: baseline fixed stop/take
1:3 (**A**) vs the same entry with a stepped trailing stop (**B**) on locked
`test_20260830_new_level` (id=126, RR 1:3, SHA `dfc855195ade…`). The single difference is
the exit rule; entries, initial 1R risk, commission 0.06%, slippage 0 and the 28-name
`run_params.tickers` universe are identical. Full period `2024-08-01` … `timestamp <
2026-08-21` (not the express window).

The trailing is a separate exit mode in `trailing.py` (`DEFAULT_STEPS`, a configurable list
of `{"trigger": <R>, "stop": <R>}`; default +2R→+1.5R, +2.5R→+2R). Steps are in R measured
from the entry, never % hardcoded. The take is unchanged; trailing only tightens the stop on
the late phase, so a trailing exit is never later than the baseline exit. The fill model
mirrors `StrategyEvaluator.on_bar` (stop before take within a bar; a step armed by the
current bar's high only affects the next bar — no intra-bar look-ahead). This module is NOT
wired into `StrategyEvaluator`, `portfolio_simulator.py`, paper or the sandbox path.

`extract_inputs.py` re-runs the unified brain over 1min candles (identical entries to the
production backtest), evaluates BOTH exits per trade on the same intra-trade path, and
reports `baseline_replay_mismatches` (must be 0). It only READS the DB (candles, 4h levels,
signals), writes no trades, verifies the four protected rows (126/36/102/118) before and
after, and is resumable via a per-ticker cache under
`reports/Vulpec/139_trailing-stop-new-level/cache/`. Price paths stay in memory;
`results.json` keeps only compact per-trade A and B outcomes, so `analysis.py` performs the
slot replay + comparison + `summary.json` + `report.md` (RU+EN) with NO database. Exact A/B
figures and the recommendation live in `summary.json` / `report.md`. Note: #129/#130 used RR
1:2 (`3b7864c4…`); #139 re-extracts for RR 1:3 — the #129 candidate book is not reused.

## 18. Trailing-grid robustness analytics (Issue #143, lattice shape by Issue #155)

`analytics/issue-143-trailing-robustness/` replays the #139 book (§17) off-engine over the 8-grid
stepped trailing lattice of schema `143-trailing-v3` (grid shape delivered by Issue #155) — `ref139`
(`+2R→+1.5R, +2.5R→+2R`, the #139 consensus), the ladders `two_step_aggressive`, `three_step_steady`,
`tight_after_take` and `late_conservative`, the stakeholder probe `ultra_late_tight` (a step 0.1R above
the lattice's latest trigger, 3R) and two single-step grids, `single_step_2_15` and `breakeven_2_0`
(stop parked at 0R) — on the same 28 tickers, locked
config id=126, 3 305 candidate trades, capital 50 000 RUB, slot 10 000 RUB, max 5 positions.
Entries come from `StrategyEvaluator` and the published 1m paths; the exit is
`analytics/issue-139-trailing-stop-new-level/trailing.apply_trailing`, so a grid delta is
attributable to the grid alone.

The shape of the lattice is machine-readable, not just prose: `summary.json.lattice` carries `groups`
(by number of steps), `pairs` (a single-step grid against the ladder that shares its first step),
`boundaries` (PO probe / single-step / break-even), `verdicts`, plus `po_grid_id`, `po_gap_r`,
`po_trigger_r` and two thresholds — `material_rub_per_trade` (50 RUB, the lattice-wide flip threshold
of #143) and `po_material_rub_per_trade` (20 RUB, the stakeholder threshold of #155 that judges the
probe grid). `backend/tests/test_issue155_analysis.py` pins the schema, the 8-grid shape, the RU report
wording and that slice.

Parity with #139 is contractual: the base grid reproduces equity 103 216.04 RUB against the
published 103 176.00 (tolerance ±206.35), PF 1.55 vs 1.54, daily MaxDD 2.72 vs 2.74 pp, with 0
exit-mechanic mismatches over 3 305 trades (max |Δ net_return| = 0.0969 pp — slot-player rounding,
not bit-for-bit).

Headline of the published run:
- Equity 95 827 … 110 434 RUB (spread 14 607 RUB — larger than the whole trailing effect measured in
  #139, +7 996 RUB); PF 1.49 … 1.60; daily MaxDD 2.42 … 4.70 pp; no GAME OVER. The best capital belongs
  to the PO probe `ultra_late_tight` (+7 218 RUB over the base grid, +1 865 RUB over the best ladder,
  ΔPnL per trade +2.0 RUB — inside the 20 RUB stakeholder threshold of #155), the worst to
  `three_step_steady`.
- Cost stress (commission 0.06 / 0.10 / 0.15 % × slippage 0 / 5 / 10 / 20 b.p., 96 runs = 8 grids × 12
  nodes): at the worst node every grid is loss-making and none reaches GAME OVER — equity drops to
  9 614 … 23 029 RUB, DD degrades by up to +79.81 pp. Costs move the book more than the choice of steps
  does.
- Walk-forward (9 three-month windows, ≥20 trades each): every grid profitable in 9/9 windows, base
  grid's worst window +745 RUB (`single_step_2_15` dips to +593 RUB) — the sign is stable, the level
  is not.
- One step vs a ladder (the shape #155 added): the only direct pair is `single_step_2_15` ↔ `ref139` —
  the added `2.5→2R` step buys +691 RUB of equity for −0.35 pp of DD and +0.8 score points; by group,
  the best single-step grid reaches 102 525 RUB against 110 434 RUB for the best ladder. The break-even
  bound `breakeven_2_0` (stop at 0R) is the lower edge of trailing's usefulness: −5 596 RUB, DD 4.70 pp,
  win rate 20.0 % — a sensitivity bound by design, not a defect.
- Composite stability score (`summary.json.grids[].robustness_score_0_100`) is effectively driven by
  absolute DD and exit-reason stability: the stress-capital and DD-degradation components collapse to
  0 for every grid in this run and walk-forward pays the same 15 to all eight (9/9 profitable windows),
  so the spread is narrow — `ref139` 47.3, `single_step_2_15` 46.5, `ultra_late_tight` 46.3,
  `two_step_aggressive` 46.2, `three_step_steady` 46.0, `tight_after_take` 45.7, `late_conservative`
  44.3, `breakeven_2_0` 43.2. The score is a summary, not an objective; no grid was optimised under it.
- Exit concordance: 69.3 % of trades keep the same outcome across all eight grids (median pairwise
  Spearman ρ 0.89, minimum 0.7497). Exit reason flips against the base grid in 1 797 of 3 305 trades
  (1 726 material at the 20 RUB threshold; `trailing` becomes the new reason 915 times, `take` 663,
  `initial_stop` 148). Against `tight_after_take` as control — the stakeholder test «worst grid not
  materially below control» — `three_step_steady` is −525 RUB.

Grid-shape debt closed: Issue #143 asked for 8–12 grids and shipped 5 multi-step ones — Issue #155
delivered the v3 shape (8 grids: six ladders + two single-step, including the break-even `stop = 0R`
bound and the `ultra_late_tight` PO probe) and made the comparison machine-readable through
`summary.json.lattice`. Still open, recorded in `report.md` §13: stress milder than specified (no MOEX
price-step / min-lot sensitivity); no `risk_reward` 1:2 sensitivity; no fixed-stop-vs-trailing
threshold at max stress (book A was not stress-run); no charts; only one direct
single-step ↔ ladder pair, so the group comparison stays indirect. Choosing the production default grid was
a Product Owner decision in #144, not a result of this package — **that decision has now been taken** (see
below). No production code path was touched by this package — engine work is #145, live-path parity gate is
#147.

### Production default grid — Product Owner decision (2026-09-08)

The Product Owner approved **`ultra_late_tight`** — `+2.0R → +1.9R`, `+2.5R → +2.4R`, `+3.0R → +2.9R` — as the
default value of `config.trailing_stop.steps` in `trading_config.TRAILING_STOP` (#144). `config.trailing_stop.enabled`
stays `false`: picking a grid switches nothing on and rewrites no locked configuration (126 / 36 / 102 / 118).
**Landed (2026-09-08, #144):** `TRAILING_STOP` + the `normalize_trailing_stop()` /
`validate_trailing_steps()` / `resolve_trailing_stop()` / `require_valid_trailing_stop()` helpers in
`backend/app/analytics/trading_config.py` — contract only: no production path calls them (this repo has no
`validate_config()` to enforce them), so the engine, the plugin and portfolio simulators, paper and live still
ignore the block (#145 / #148 / #151), a malformed ladder is still accepted at save time until #146 / #149 gate
it, and a config without the key behaves exactly as before. Full field contract: §6; tests:
`backend/tests/test_trailing_contract.py` (33).

- Why it won: best capital of the lattice, 110 434 RUB (+7 218 over `ref139`, +1 865 over the best other ladder), PF 1.60,
  3 162 trades, win rate 42.6 %, the best average walk-forward profit factor of the lattice (1.62) with a +1 366 RUB
  worst-window floor against +745 for the base grid (`three_step_steady` floors higher, at +1 512), more equity than the
  base at every node of the cost-stress lattice (19 364 vs 16 708 RUB at the worst node, commission 0.15 % + 20 b.p.;
  DD degrades +64.45 vs +68.60 pp), and one of the smallest behavioural diffs in the lattice (141 exit-reason flips
  = 4.3 % against the base grid, Spearman ρ 0.9966 — only `two_step_aggressive` is closer, at 126 flips / 3.8 %) —
  the gain comes from the shape of the rule, not from one lucky trade (ΔPnL per trade +2.0 RUB).
- What was traded away: composite stability score 46.3 versus 47.3 for `ref139` and daily MaxDD 3.06 pp versus
  2.72 pp. The score is a summary of these eight grids on one book and one period, not an objective (report §4),
  and the equity, average walk-forward PF and stress-node gains were judged worth the +0.34 pp of drawdown.
- What stays unchanged: `ref139` (the #139 grid) remains the **parity anchor** — #147 injects it explicitly, it is
  not a production default — and the published artifacts of #139 and #143 are frozen evidence. #143 and #155 stay
  closed, with a pointer comment recording the decision.
- Residual risk owned by follow-ups: the step margin is **0.1R** (not 0.5R as in `ref139`), so slippage or a gap on
  the synthetic stop of `LiveExecutor` consumes a visible share of the locked profit. #151 owes a defensive price
  step and #152 owes the break-even slippage measured against 0.1R; the leave / tune / rollback verdict of #152 can
  move the default only back through the Product Owner.

