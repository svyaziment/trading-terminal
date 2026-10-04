# SignalEngine AND-filters in StrategyEvaluator

> **Source:** project-context.md sections 16 + handover.md sections 20
> **Last refreshed:** 2026-10-04, task-346

## 16. SignalEngine AND-filters in StrategyEvaluator

Issue #79 connects the ten Signals-tab `BasePattern` classes to `StrategyEvaluator` as AND-filters after `levels_reversal` (stop/take stay levels-only). The path is fixed and must not be mixed:

- `signal_4h_buy` continues to look up `trading.signals` (4h BUY aggregate). It is not refactored.
- SignalEngine ids are evaluated **inline** with `SignalEngine.process_dataframe` / `BasePattern.evaluate` on `trading.indicators` for the selected HTF. They never look up `trading.signals` by `pattern_name`.
- `rsi_oversold` remains the 1min RSI<30 filter and is not a substitute for `MR_RSI_Reversal`.

`timeframe` is a select like `level_timeframe`. Supported values match SignalEngine thresholds: 30min, 1h, 2h, 4h, 1d, 1w (default 4h). Full Lab schemas live in `SIGNAL_ENGINE_PATTERN_SCHEMAS` / `PATTERN_REGISTRY` (`pattern_registry.py`); numeric defaults are the 4h `get_thresholds` (or `evaluate` literals for PA). `normalize_patterns` stores those params, while `StrategyEvaluator` currently keys inline evaluate by `timeframe` only. The filter uses the last *closed* HTF bar (bar open + TF delta <= current 1min ts) so backtests do not look ahead into a still-forming bucket. Missing HTF indicator rows reject the entry. `2h` is in the contract because patterns define thresholds for it, but the current candle/indicator pipeline does not persist 2h, so that selection currently yields no trades.

`build_strategy_context` precomputes BUY timestamps per enabled filter and passes `signal_filter_series` into `StrategyEvaluator.load_context` (backtest, paper, live). Default `levels_reversal` + `signal_4h_buy` (including locked `test_20260731`) does not enable any SignalEngine id, so trade lists stay unchanged. E2E coverage: `tests/test_signal_pattern_e2e.py` (`levels_reversal` + one registry id such as `PA_Engulfing` on 4h).

## 20. Operating SignalEngine Strategy Lab filters

- Entry points: `app.analytics.signal_pattern_filters` (inline evaluate + last-closed HTF) and `StrategyEvaluator.check_entry`. Context is built by `build_strategy_context`.
- Path rule: `signal_4h_buy` looks up `trading.signals`; the ten SignalEngine ids call `BasePattern.evaluate` on `trading.indicators`. Do not mix a `pattern_name` lookup into the SignalEngine path. Do not replace `MR_RSI_Reversal` with `rsi_oversold`.
- `timeframe` contract: `SIGNAL_PATTERN_TIMEFRAME_PARAM` in `pattern_registry.py` (select, options 30min/1h/2h/4h/1d/1w, default 4h). Full schemas are in `SIGNAL_ENGINE_PATTERN_SCHEMAS`; 4h defaults match current SignalEngine `get_thresholds` / PA `evaluate` literals. `normalize_patterns` fills them; evaluator still keys inline evaluate by `timeframe` only.
- How to enable in the constructor: add a SignalEngine chip from `GET /api/patterns` (do not hardcode the ten ids in `StrategyLab.tsx`). Chips are grouped by API `category` with RU titles. Timeframe and pattern params are set in `PatternSettingsModal` (no extra global TF selector). Save runs `normalize_patterns`; the same config is used by `strategy_backtest`, paper (`get_active_paper_strategy` → `StrategyEvaluator`), and live. Do not overwrite locked `test_20260731`. The two-chip fallback (`levels_reversal` + `signal_4h_buy`) is only used when the patterns API is empty; a live registry is never replaced by that list.
- Filter uses the last closed HTF bar. Missing indicator rows reject the entry. `2h` is in the contract but is not persisted by the current aggregator/indicator pipeline.
- Locked paper strategy `test_20260731` must stay `levels_reversal` + `signal_4h_buy` only.
- Unit tests: `cd backend && python -m pytest -q tests/test_signal_engine_filters.py tests/test_pattern_registry.py tests/test_signal_pattern_e2e.py`.
