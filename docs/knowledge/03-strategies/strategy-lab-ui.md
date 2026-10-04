# Operating the Level Breakout Retest Lab chip

> **Source:** handover.md sections 24, 26, 30
> **Last refreshed:** 2026-10-04, task-346

## 24. Operating the Level Breakout Retest Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts`, `patternValidation.ts`.
- Enable from the **Пробой** group. Visible name is API `label` («Пробой уровня с ретестом»); EN `label_en` («Level Breakout Retest») is in the tooltip and under the modal title. Icon `breakout_up` (arrow through a level) is also from the API.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the six params in the frontend. Schema: `level_timeframe` (1h/4h/1d), `retest_window_bars` (1–100), `retest_zone_atr` (0.1–2.0), `entry_trigger_bullish`, `stop_atr` (0.5–3.0), `risk_reward` (≥1). Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Combine with `levels_reversal` (still required for the support-zone path) and optional SignalEngine / `signal_4h_buy` filters. AND logic is unchanged. Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: after a confirmed resistance break you want a retest entry (role reversal) instead of (or in addition to) a native support-zone entry. Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- This AND-filter is **not** a substitute for `levels_sr_breakout` (handover §25). Epic #115 isolates the composite; do not combine the two chips as “the new strategy”.

## 26. Operating the Composite S/R Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts` (`resolveConfirmWindows`), `patternValidation.ts`. Icon map: `PatternIcon.tsx` keyed by API `icon`, not by pattern id.
- Enable from the **Уровни** group (not **Пробой**). Visible name is API `label` («Поддержка + пробой сопротивления»); EN `label_en` («Support Reversal + Resistance Breakout») is in the tooltip and under the modal title. Icon `support_breakout` (support line + resistance break) is also from the API and must stay distinct from `breakout_up`.
- This chip **replaces** `levels_reversal` for the new strategy. Isolated run: turn on `levels_sr_breakout` and optionally `signal_4h_buy` / SignalEngine; leave `levels_reversal` and `level_breakout_retest` off. If both levels chips are on, the backend composite wins (one support path) — do not treat that as a third AND.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the param list in the frontend. Schema = all `levels_reversal` fields + retest fields. Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Top-level `config.confirm_windows` is taken from the enabled schema that owns that param; the composite wins over `levels_reversal` (same as backend `_LEVELS_CONFIRM_PATTERN_IDS`). Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: you want one Lab engine that enters on native support **or** on a confirmed resistance retest. Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated AFKS smoke (Issue #119): handover §27. Lab-universe A/B (Issue #124): handover §28. Support-only engine (Issue #127): handover §29. Lab chip (Issue #128): handover §30. Isolated support universe (Issue #129): handover §31. Portfolio 50k (Issue #130): handover §32. Do not treat either package as a paper verdict.

## 30. Operating the Support-with-tracker Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts` (`resolveConfirmWindows`, `LEVELS_CONFIRM_PATTERN_IDS`), `patternValidation.ts`. Icon map: `PatternIcon.tsx` keyed by API `icon`, not by pattern id.
- Enable from the **Уровни** group (not **Пробой**). Visible name is API `label` («Поддержка с трекером»); EN `label_en` («Support Reversal (tracker veto)») is in the tooltip and under the modal title. Icon `support_tracker` (support line + tracker watching the zone, no breakout arrow) is also from the API and must stay distinct from `support_breakout` and `breakout_up`.
- This chip **replaces** `levels_reversal` / `levels_sr_breakout` for this strategy. Isolated run: turn on `levels_sr_support` and optionally `signal_4h_buy` / SignalEngine; leave `levels_reversal`, `levels_sr_breakout`, and `level_breakout_retest` off. If `levels_sr_breakout` is also on, the backend composite wins. If `levels_sr_support` and `levels_reversal` are both on, the new id wins (one support path) — do not treat either mix as a third AND.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the param list in the frontend. Schema = `levels_reversal` fields only — no retest keys. Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Top-level `config.confirm_windows` is taken from the enabled schema that owns that param; priority is composite > support-with-tracker > `levels_reversal` (same as backend `_LEVELS_CONFIRM_PATTERN_IDS`). Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: you want the #124 B-support path (tracker-aware veto, no resistance retest). Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated vs #124 B-support is Issue #129 (handover §31). Portfolio 50k is Issue #130 (handover §32). Do not treat this chip as a paper verdict.
