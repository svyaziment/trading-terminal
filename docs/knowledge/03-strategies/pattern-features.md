# Patterns (10 total)

> **Source:** project-context.md sections 6 + handover.md sections 21, 22, 23, 27, 28, 29
> **Last refreshed:** 2026-10-04, task-346

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
- **Trailing-stop exit contract (Issue #144, Epic #142)**: `config.trailing_stop` is the top-level exit-policy block whose schema, defaults and validation helpers live in `trading_config.py`. Shape: `{"enabled": bool, "steps": [{"trigger": 2.0, "stop": 1.9}, ...]}` in R multiples measured from the entry — there is **no `take_partial`** and no `trigger_r` / `lock_r` naming, because a partial take is not part of the approved grid. `validate_trailing_steps()` never raises: it returns the stable reason codes `trailing_disabled` / `trailing_step_invalid` / `trailing_not_monotonic` / `trailing_too_many_steps` (empty list = accepted), `normalize_trailing_stop()` canonicalizes, and `resolve_trailing_stop(config)` hands every consumer `{'enabled', 'steps', 'reasons'}`. Bounds come from `TRAILING_STOP` and are the single source of truth: `0 < trigger <= 3.5`, `0 <= stop <= 3.0`, always `stop < trigger`, at most `max_steps` = 6 steps, and a step must be a dict (a list of pairs is not accepted). There is deliberately **no minimum trigger–stop gap** — the shipped ladder lives on 0.1R, so a "gap ≥ 0.5R" rule would reject the default. Default policy `TRAILING_STOP` = `enabled=false`, steps `2.0→1.9 / 2.5→2.4 / 3.0→2.9` — the `ultra_late_tight` grid the Product Owner approved on 2026-09-08 (handover §35, §18) and shipped **disabled**, so no config changes behaviour. An absent key, `None`, `{}` or a non-dict block mean "no trailing" with an empty reasons list; `enabled=true` with no usable steps reports `trailing_disabled`. `enabled` is strict (only a real bool or `'1' / 'true' / 'yes' / 'on'` arms the ladder) and normalization keeps float precision — 1.9 / 2.4 / 2.9 are never rounded to 0.5R. **Applied since #145; still no write-path gate:** `backend/app/analytics/trailing_stop.py` reads the block through `resolve_trailing_stop()` and `StrategyEvaluator.on_bar`, the `levels_reversal` plugin, `portfolio_simulator` and walk-forward arm the ladder from it (§19); `EXIT_TRAILING` is emitted from #145 on. The engine **fails safe** — a ladder the validator refuses is never armed, so that position is managed exactly as it was before #145. `validate_config()` still does not exist in this repository, so a malformed block keeps saving into `trading.strategies` until #146 / #149 call `require_valid_trailing_stop()` on the write path, and `paper_trader` reads the block since #148 (in-memory `TrailingState`); `live_executor` still ignores it (#151). The legacy pattern-level `trailing_stop` / `trailing_step` params in `pattern_registry.py` are a different contract untouched by #144. Tests: `backend/tests/test_trailing_contract.py` (33 — the contract) and `backend/tests/test_trailing_stop.py` (52 — the #145 behaviour matrix).

## 21. Operating Pattern Chart Preview (Epic #87)

- Entry point: `POST /api/patterns/preview` in `strategy_jobs.py`; logic in `app.analytics.pattern_preview`.
- Request: `ticker`, `pattern_id`, draft `params`, `date_from`, `date_to`. Timeframe comes from params (`level_timeframe` for `levels_reversal`, `timeframe` for SignalEngine ids).
- Response: `status` (`ok` / `empty` / `error` / `unsupported`), `candles`, typed `overlays` (`ray`, `band`, `line`, `marker`). Issue #88 implements `levels_reversal`: all levels with `defined_ts` in the window; each level emits a `ray` from `defined_ts` to the last visible bar plus a `band` for the ATR zone. Do not emulate rays with infinite price lines.
- Unknown `pattern_id` returns `status=error` without 500. Missing candles (including unsupported `2h`) returns `status=empty` with a clear message.
- Other pattern ids return `status=unsupported` with candles only until #91 adds overlay renderers. Frontend chart work is #89–#92.
- Unit test: `cd backend && python -m pytest -q tests/test_pattern_preview.py`.

## 22. Operating the Levels State Machine

- Entry points: `LevelsTracker` / `get_levels_with_state` / `is_broken` in `levels_engine.py`. Initialise from `get_levels()` (`build_levels` alias). Feed bars of the **same** timeframe as the levels (typically 4h). In-memory only — no table, no migration.
- Thresholds come only from `LEVEL_STATE_MACHINE` in `trading_config.py`: `breakout_buffer_atr=0.25`, `confirm_bars=2`, `min_penetration_atr=0.5`, `zone_extension_atr=0.5`. `zone_extension_atr` documents the current `build_levels` zone width; the tracker does not recompute `zone_lower`/`zone_upper`.
- Resistance break: last `confirm_bars` closes all above `zone_upper`, last close above `zone_upper + buffer×ATR`, and max(window) at least `zone_upper + min_penetration×ATR`. Support is symmetric below `zone_lower`. First close back inside the native zone after a break flips `broken_up → flipped_support` / `broken_down → flipped_resistance`. Failed breakouts are not reverted to `active` in this iteration.
- `overlapping_resistance_zone_at` vetoes only `active` resistances when a `state` column exists, and skips `tracker.is_broken(level_id)` when a tracker is passed (Issue #107). Pass a tracker snapshot taken **after** `update()` through closed HTF bars only. Frames without `state` and callers that omit `tracker` keep the Issue #97 behaviour.
- `StrategyEvaluator` constructs `LevelsTracker` when `level_breakout_retest`, `levels_sr_breakout`, or `levels_sr_support` is in `config.patterns`. Locked `test_20260731` enables none of them. `bars_since_breakout(level_id)` counts HTF bars since the confirmed break.
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_state_machine.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py`.

## 23. Operating the Level Breakout Retest Pattern

- Entry points: `check_breakout_retest` / `evaluate_level_breakout_retest` in `patterns/level_breakout_retest.py`; AND-filter `_check_level_breakout_retest` in `StrategyEvaluator`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/` (that would shadow `breakout.py`).
- Lab schema: `PATTERN_REGISTRY['level_breakout_retest']` (also copied onto `SIGNAL_ENGINE_PATTERN_SCHEMAS` for the Issue #107 AC / `GET /api/patterns`). Defaults: `level_timeframe=4h`, `retest_window_bars=20`, `retest_zone_atr=0.5`, `entry_trigger_bullish=true`, `stop_atr=1.0`, `risk_reward=2.0`. The bullish-body ratio `0.6` is not Lab-tunable; it lives in `LEVEL_BREAKOUT_RETEST` in `trading_config.py`.
- Criteria (all must hold): tracker state `broken_up` or `flipped_support`; close in `[level ± retest_zone_atr×ATR]`; close ≥ broken `level_price`; `bars_since_breakout <= retest_window_bars`; if `entry_trigger_bullish`, `close > prev_high` OR bullish body.
- Stop/take: `stop = entry − stop_atr×ATR`, `take = entry + risk_reward×(entry−stop)`. When the pattern is enabled these replace levels stop/take; the top-level config RR filter is not applied on top (pattern RR already encodes the ratio).
- Context: `build_strategy_context` returns `htf_bars` (same TF as levels). The evaluator feeds only HTF bars whose close ≤ current 1min ts (no lookahead). Paper/live `load_context` / `update_context` pass this frame through. Lab/plugin path: `portfolio_backtest` puts the same frame on `MarketContext.htf_bars` (Issue #116); do not rely on `candles_4h`.
- Veto interaction: with the pattern on, a broken resistance is no longer an opposing zone (`is_broken`). Without the pattern, every overlapping resistance still vetoes (locked `test_20260731`).
- Composability: AND with `levels_reversal` (still required for the support-zone path) and with SignalEngine / `signal_4h_buy` filters. Lab chip: handover §24. `GET /api/patterns` is the source of names, hints, icon, and param schema.
- Do not rewrite locked `test_20260731`.
- Unit tests: `cd backend && python -m pytest -q tests/test_level_breakout_retest.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py`.

## 27. Operating the AFKS composite smoke

- Package: `analytics/issue-119-afks-sr-breakout-smoke/`. Isolated ticker, not 50k slots.
- A = `levels_reversal` + `signal_4h_buy` (same geometry as #103). B = only `levels_sr_breakout` + `signal_4h_buy`. Period `2024-08-01` … `timestamp < 2026-08-21`.
- Primary engine is `run_strategy_backtest` so trades keep `source`. The Lab plugin path matches n/PF after #116 but currently drops `source` from plugin trades.
- B-support can exceed A: the composite passes `LevelsTracker` into the veto, so a broken resistance no longer blocks a support entry.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without DB: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py`. Full re-run: `python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py`.
- Units: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.

## 28. Operating the Lab-universe composite A/B

- Package: `analytics/issue-124-sr-breakout-universe/`. Isolated 28-ticker Lab universe (`get_big_tickers`), not live top-5 and not `run_params.tickers`.
- A/B configs are the published #119 SHA pair. Period `2024-08-01` … `timestamp < 2026-08-21`. Engine is `run_strategy_backtest` so trades keep `source`.
- Isolated B is larger than A for two reasons: path B (`levels_sr_breakout_resistance`) plus extra support after the tracker-aware veto. Do not treat B-support n as a bit-for-bit copy of A.
- Optional 50k / 10k / max-5 replay of B candidates is a **separate** block. Do not mix that PF/equity with isolated ticker PF. It is not a paper verdict.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without a new backtest: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Full re-run: `python analytics/issue-124-sr-breakout-universe/extract_inputs.py` (resumable).
- Units: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.

## 29. Operating the Support-with-tracker Pattern (`levels_sr_support`)

- Entry points: `PATTERN_ID` / `SOURCE` in `patterns/levels_sr_support.py`; support-only path in `StrategyEvaluator._check_sr_support_entry`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/`.
- Lab schema: `PATTERN_REGISTRY['levels_sr_support']` (also on `SIGNAL_ENGINE_PATTERN_SCHEMAS` for `GET /api/patterns`). Category `levels` (next to `levels_reversal` and `levels_sr_breakout`, not in breakout). Icon `support_tracker` (must stay distinct from `breakout_up` and `support_breakout`). Params = **only** `levels_reversal` fields — no retest keys. Lab chip: handover §30. Do not hardcode the param keys in TSX.
- Isolated run: `config.patterns` contains `levels_sr_support` and optionally `signal_4h_buy` / SignalEngine ids. `levels_reversal` is **not** required. `run_strategy_backtest` treats this id as a sufficient entry engine.
- Order in `check_entry` after session / HTF / `_sync_tracker`: (1) if `levels_sr_breakout` is on, the composite wins (unchanged); (2) else if `levels_sr_support` is on — common AND, then support zone + confirm + veto of *active* resistance with `tracker=self._tracker` → `source=levels_sr_support`, levels stop/take, top-level RR filter. **Do not** call `check_breakout_retest` on this id.
- Both chips (`levels_sr_support` + `levels_sr_breakout`): composite wins. `levels_sr_support` + `levels_reversal`: the new id wins (one support path, no doubling). `_LEVELS_CONFIRM_PATTERN_IDS` order is composite > support-with-tracker > `levels_reversal`.
- Difference vs `levels_reversal`: tracker is passed into the veto, so a broken resistance no longer blocks a valid support entry. Difference vs `levels_sr_breakout`: no path B / retest.
- Tracker / `htf_bars`: same feed as #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit tests do not depend on the Lab UI.
- Locked `test_20260731` must not enable this id (paper/live veto and levels stop/take stay bit-for-bit).
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_levels_sr_breakout.py tests/test_strategy_plugin.py`.
