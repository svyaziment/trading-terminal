# Operating the Composite S/R Pattern (`levels_sr_breakout`)

> **Source:** handover.md sections 25
> **Last refreshed:** 2026-10-04, task-346

## 25. Operating the Composite S/R Pattern (`levels_sr_breakout`)

- Entry points: `PATTERN_ID` / sources in `patterns/levels_sr_breakout.py`; OR logic in `StrategyEvaluator._check_sr_breakout_entry`. Path B reuses `check_breakout_retest`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/`.
- Lab schema: `PATTERN_REGISTRY['levels_sr_breakout']` (also on `SIGNAL_ENGINE_PATTERN_SCHEMAS` for `GET /api/patterns`). Category `levels` (next to `levels_reversal`, not in breakout). Icon `support_breakout` (must stay distinct from `breakout_up`). Params = all `levels_reversal` fields + retest fields (`retest_window_bars`, `retest_zone_atr`, `entry_trigger_bullish`, `stop_atr`, `risk_reward`). Lab chip: handover §26. Do not hardcode the param keys in TSX.
- Isolated run: `config.patterns` contains `levels_sr_breakout` and optionally `signal_4h_buy` / SignalEngine ids. `levels_reversal` is **not** required. `run_strategy_backtest` treats the composite as a sufficient entry engine.
- Order in `check_entry` after session / HTF / `_sync_tracker`: (1) common AND (`signal_4h_buy`, SignalEngine, 1min indicator filters); (2) path B — `check_breakout_retest` → `source=levels_sr_breakout_resistance`, ATR stop/take, no second config RR filter; (3) else path A — support zone + confirm + veto of *active* resistance with tracker (`source=levels_sr_breakout_support`, levels stop/take, top-level RR filter). If both would fire, path B wins.
- Both chips present (`levels_reversal` + `levels_sr_breakout`): composite wins — one support path, no doubling.
- Do not AND with `level_breakout_retest` as a replacement for this engine. The Epic #105 AND-filter contract stays unchanged.
- Tracker / `htf_bars`: same feed as #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit tests do not depend on the Lab UI.
- Locked `test_20260731` must not enable this id (paper/live veto and levels stop/take stay bit-for-bit).
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.
