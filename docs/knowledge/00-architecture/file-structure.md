# File Structure

> **Source:** project-context.md sections 2
> **Last refreshed:** 2026-10-04, task-346

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
│ │ │ ├── live_schema.py # live_positions schema contract (30 columns / 7 statuses) + fail-fast drift validation (#173)
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
│ │ ├── trailingStop.ts # Trailing ladder parse/validate/payload for the Lab editor (#146); pure, no numbers
│ │ ├── exitReasons.ts # Exit-reason labels/tones incl. `trailing` for the Lab trade table (#146)

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
│ ├── strategy/ # levels-reversal-strategy.md, paper-trading.md, live-trading.md, testing-rules.md, backtest-report.md (+ .ru)
│ └── refresh/context_collector.py # Context collector for agent tasks
├── scripts/ # Task scripts (gitignored) + refresh scanners
├── start_processes.sh # Start paper trading processes (catch-up + 4 processes)
├── stop_processes.sh # Stop paper trading processes
└── docker-compose.yml # agent + backend services (backend mounts ./reports)
