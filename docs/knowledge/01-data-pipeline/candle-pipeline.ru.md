# Эксплуатация композитного S/R паттерна (`levels_sr_breakout`)

> **Source:** handover.ru.md sections 25
> **Last refreshed:** 2026-10-04, task-346

## 25. Эксплуатация композитного S/R паттерна (`levels_sr_breakout`)

- Точки входа: `PATTERN_ID` / `source` в `patterns/levels_sr_breakout.py`; OR-логика в `StrategyEvaluator._check_sr_breakout_entry`. Путь B переиспользует `check_breakout_retest`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/`.
- Схема Lab: `PATTERN_REGISTRY['levels_sr_breakout']` (также в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для `GET /api/patterns`). Категория `levels` (рядом с `levels_reversal`, не в breakout). Иконка `support_breakout` (должна отличаться от `breakout_up`). Параметры = все поля `levels_reversal` + поля ретеста (`retest_window_bars`, `retest_zone_atr`, `entry_trigger_bullish`, `stop_atr`, `risk_reward`). Чип Lab: handover §26. Не хардкодить ключи params в TSX.
- Изолированный прогон: в `config.patterns` есть `levels_sr_breakout` и опционально `signal_4h_buy` / id SignalEngine. `levels_reversal` **не** обязателен. `run_strategy_backtest` считает композит достаточным движком входа.
- Порядок в `check_entry` после сессии / HTF / `_sync_tracker`: (1) общие AND (`signal_4h_buy`, SignalEngine, 1min индикаторы); (2) путь B — `check_breakout_retest` → `source=levels_sr_breakout_resistance`, ATR stop/take, без второго RR-фильтра конфига; (3) иначе путь A — зона поддержки + confirm + вето *активного* сопротивления с трекером (`source=levels_sr_breakout_support`, levels stop/take, верхнеуровневый RR). Если сработали оба — побеждает путь B.
- Оба чипа (`levels_reversal` + `levels_sr_breakout`): побеждает композит — один support-путь, без удвоения.
- Не AND с `level_breakout_retest` как заменой этому движку. Контракт AND-фильтра эпика #105 не меняется.
- Трекер / `htf_bars`: тот же корм, что в #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit-тесты не зависят от UI Lab.
- Locked `test_20260731` этот id не включает (paper/live вето и levels stop/take остаются бит-в-бит).
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.
