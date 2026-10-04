# AND-фильтры SignalEngine в StrategyEvaluator

> **Source:** project-context.ru.md sections 16 + handover.ru.md sections 20
> **Last refreshed:** 2026-10-04, task-346

## 16. AND-фильтры SignalEngine в StrategyEvaluator

Задача #79 подключает десять классов `BasePattern` вкладки Signals к `StrategyEvaluator` как AND-фильтры после `levels_reversal` (stop/take по-прежнему только от levels). Путь фиксирован и не смешивается:

- `signal_4h_buy` по-прежнему читает `trading.signals` (агрегат 4h BUY). Его не рефакторят.
- id SignalEngine считаются **inline** через `SignalEngine.process_dataframe` / `BasePattern.evaluate` по `trading.indicators` выбранного HTF. Lookup `trading.signals` по `pattern_name` не используется.
- `rsi_oversold` остаётся 1min-фильтром RSI<30 и не заменяет `MR_RSI_Reversal`.

`timeframe` — select, как `level_timeframe`. Поддерживаемые ТФ совпадают с порогами SignalEngine: 30min, 1h, 2h, 4h, 1d, 1w (по умолчанию 4h). Полные схемы Lab живут в `SIGNAL_ENGINE_PATTERN_SCHEMAS` / `PATTERN_REGISTRY` (`pattern_registry.py`); числовые дефолты — 4h `get_thresholds` (для PA — литералы `evaluate`). `normalize_patterns` сохраняет эти параметры, а `StrategyEvaluator` сейчас ключует inline evaluate только по `timeframe`. Фильтр смотрит последнюю *закрытую* HTF-свечу (open бара + длительность ТФ <= текущий 1min ts), чтобы бэктест не заглядывал в ещё формирующийся бакет. Отсутствующие HTF-индикаторы отклоняют вход. `2h` есть в контракте, потому что у паттернов есть пороги, но пайплайн свечей/индикаторов сейчас 2h не пишет — выбор этого ТФ не даёт сделок.

`build_strategy_context` заранее считает BUY-метки по каждому включённому фильтру и передаёт `signal_filter_series` в `StrategyEvaluator.load_context` (бэктест, paper, live). Дефолт `levels_reversal` + `signal_4h_buy` (включая locked `test_20260731`) не включает ни один SignalEngine id, поэтому список сделок не меняется. E2E: `tests/test_signal_pattern_e2e.py` (`levels_reversal` + один id из реестра, например `PA_Engulfing` на 4h).

## 20. Эксплуатация фильтров SignalEngine в Strategy Lab

- Точки входа: `app.analytics.signal_pattern_filters` (inline evaluate + последний закрытый HTF) и `StrategyEvaluator.check_entry`. Контекст собирает `build_strategy_context`.
- Правило пути: `signal_4h_buy` читает `trading.signals`; десять id SignalEngine вызывают `BasePattern.evaluate` на `trading.indicators`. Не подмешивать lookup по `pattern_name`. Не подменять `MR_RSI_Reversal` фильтром `rsi_oversold`.
- Контракт `timeframe`: `SIGNAL_PATTERN_TIMEFRAME_PARAM` в `pattern_registry.py` (select, 30min/1h/2h/4h/1d/1w, по умолчанию 4h). Полные схемы — в `SIGNAL_ENGINE_PATTERN_SCHEMAS`; дефолты 4h совпадают с текущими `get_thresholds` SignalEngine / литералами `evaluate` у PA. `normalize_patterns` их заполняет; evaluator по-прежнему ключует inline evaluate только по `timeframe`.
- Как включить в конструкторе: добавьте чип SignalEngine из `GET /api/patterns` (не хардкодить десять id в `StrategyLab.tsx`). Чипы сгруппированы по `category` из API с RU-заголовками. Таймфрейм и параметры паттерна задаются в `PatternSettingsModal` (отдельный глобальный TF-селектор не нужен). Save вызывает `normalize_patterns`; тот же конфиг идёт в `strategy_backtest`, paper (`get_active_paper_strategy` → `StrategyEvaluator`) и live. Locked `test_20260731` не перезаписывать. Fallback из двух чипов (`levels_reversal` + `signal_4h_buy`) используется только если API паттернов пуст; живой реестр этим списком не подменяется.
- Фильтр смотрит последнюю закрытую HTF-свечу. Нет строк индикаторов — вход отклоняется. `2h` есть в контракте, но текущий пайплайн агрегации/индикаторов его не пишет.
- Locked paper-стратегия `test_20260731` должна оставаться только `levels_reversal` + `signal_4h_buy`.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_signal_engine_filters.py tests/test_pattern_registry.py tests/test_signal_pattern_e2e.py`.
