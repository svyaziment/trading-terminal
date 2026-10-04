# Паттерны (10 всего)

> **Source:** project-context.ru.md sections 6 + handover.ru.md sections 21, 22, 23, 27, 28, 29
> **Last refreshed:** 2026-10-04, task-346

## 6. Паттерны (10 всего)

Вкладка Signals по-прежнему пишет десять классов `BasePattern` ниже в `trading.signals` (confidence, BUY/SELL, total_signals). Эта таблица **не** является путём Lab-фильтра, кроме `signal_4h_buy`.

| Категория | Паттерн | Описание |
|---|---|---|
| Тренд | Trend_SMA_Alignment | Выравнивание SMA (20/50/200) |
| Возврат к среднему | MR_RSI_Reversal | Разворот RSI из перекупленности/перепроданности |
| Пробой | BO_BB_Squeeze | Сжатие Bollinger Bands |
| Объём | VOL_Spike | Всплеск объёма (>2x от среднего) |
| Объём | VOL_Low_Pullback | Откат на низком объёме |
| Ценовое действие | PA_Hammer | Молот (бычий разворот) |
| Ценовое действие | PA_HangingMan | Повешенный (медвежий разворот) |
| Ценовое действие | PA_Engulfing | Поглощение (бычье/медвежье) |
| Ценовое действие | PA_ThreeWhiteSoldiers | Три белых солдата (бычий) |
| Ценовое действие | PA_ThreeBlackCrows | Три чёрные вороны (медвежий) |

Паттерны Strategy Lab (config-driven, логика AND, один конфиг для бэктеста / paper / live):
- `levels_reversal` — обязателен для классического пути поддержки; 4h зона поддержки + подтверждение; задаёт stop/take. Задача #97: `check_entry` отклоняет бар, если 1min close лежит в активной зоне сопротивления (`overlapping_resistance_zone_at`); это дефект, а не role-reversal. Задача #106: при колонке `state` вето пропускает зоны не в `active`. Задача #107: `StrategyEvaluator` передаёт `LevelsTracker` в вето если включён `level_breakout_retest`, `levels_sr_breakout` или `levels_sr_support`; у `build_levels` колонки `state` нет, locked `test_20260731` остаётся бит-в-бит. Не обязателен в `config.patterns`, если движок входа — `levels_sr_breakout` или `levels_sr_support`.
- `levels_sr_support` — изолированный Lab-движок входа (эпик #126 / задача #127): **только** путь B-support из #124. Та же геометрия поддержки, что у `levels_reversal` (зона + расширение 0.5×ATR + confirm + levels stop/take + верхнеуровневый RR) плюс вето #97 *активного* сопротивления **с** `LevelsTracker` (`source=levels_sr_support`). **Не** вызывает `check_breakout_retest`. Изолированный прогон: в `config.patterns` есть `levels_sr_support` (опционально `signal_4h_buy`) **без** `levels_reversal` / `levels_sr_breakout` / `level_breakout_retest`. Если одновременно включён `levels_sr_breakout` — побеждает композит. Если одновременно `levels_sr_support` и `levels_reversal` — побеждает новый id (один support-путь). Не SignalEngine id. Категория `levels`, иконка `support_tracker` (не `breakout_up` и не `support_breakout`). Схема = только поля `levels_reversal`, без ключей ретеста; `normalize_patterns` заполняет дефолты. Файл: `patterns/levels_sr_support.py` (не под `patterns/breakout/`). Чип Lab в группе **Уровни** из `GET /api/patterns`; `PatternSettingsModal` и валидация остаются schema-driven — не хардкодить список params в TSX. На locked `test_20260731` оставлять выключенным.
- `levels_sr_breakout` — изолированный Lab-движок входа (эпик #115 / задачи #117 и #118), OR двух путей. Путь A = геометрия поддержки `levels_reversal` + вето *активного* сопротивления (`source=levels_sr_breakout_support`). Путь B = `check_breakout_retest` без нативной зоны поддержки (`source=levels_sr_breakout_resistance`, stop/take ATR × RR; верхнеуровневый RR конфига повторно не накладывается). Общие AND: сессия, HTF, опционально `signal_4h_buy` / SignalEngine. Если на одном баре сработали оба пути — побеждает путь B. Если в `config.patterns` одновременно `levels_reversal` и `levels_sr_breakout` — побеждает композит (один support-путь, без удвоения). Не AND с `level_breakout_retest` как заменой. Не SignalEngine id. Категория `levels`, иконка `support_breakout` (не `breakout_up`). Схема = все поля `levels_reversal` + поля ретеста; `normalize_patterns` заполняет дефолты. Файл: `patterns/levels_sr_breakout.py` (не под `patterns/breakout/`). Чип Lab в группе **Уровни** из `GET /api/patterns`; `PatternSettingsModal` и валидация остаются schema-driven — не хардкодить список params в TSX. Чип заменяет `levels_reversal` для новой стратегии; на locked `test_20260731` оставлять выключенным.
- `level_breakout_retest` — Lab AND-фильтр после `levels_reversal` (эпик #105 / задачи #107 и #109). Подтверждённый пробой сопротивления + ретест в `[level ± retest_zone_atr×ATR]` + close ≥ пробитого уровня + бычий триггер. Stop/take = ATR × RR из параметров паттерна. Не SignalEngine inline-evaluate id (не добавлять в `SIGNAL_ENGINE_PATTERN_IDS`). Схема в `PATTERN_REGISTRY` (опциональные `label_en` / `hint_en` / `icon`) и в `GET /api/patterns`. Strategy Lab рендерит чип группы «Пробой» и поля `PatternSettingsModal` из этого payload — не хардкодить шесть параметров во frontend. Файл: `patterns/level_breakout_retest.py` (не класть в `patterns/breakout/` — затенит `breakout.py` / `BO_BB_Squeeze`). Задача #116: plugin-путь Lab обязан прокидывать `htf_bars`, иначе трекер не покидает `active` (ноль сделок пробоя). Другой контракт, чем `levels_sr_breakout` — не заменять этот AND-фильтр композитом.
- `signal_4h_buy` — агрегат 4h BUY из `trading.signals` (ТФ фиксирован; не рефакторится).
- `rsi_oversold` / `macd_bullish` / `bb_lower` — 1min индикаторные AND-фильтры. `rsi_oversold` не является `MR_RSI_Reversal`.
- Десять id SignalEngine выше — AND-фильтры на последней закрытой HTF-свече через inline `BasePattern.evaluate` по `trading.indicators`. Схемы из `GET /api/patterns` (select `timeframe` 30min/1h/2h/4h/1d/1w, по умолчанию 4h, плюс числовые дефолты 4h). Таймфрейм задаётся в модалке настроек. `StrategyLab.tsx` группирует чипы по `category` из API (RU-заголовки: levels / signal / trend / price_action / volume / mean_reversion / breakout). Десять id SignalEngine и списки параметров `level_breakout_retest` / `levels_sr_breakout` / `levels_sr_support` не хардкодятся; fallback из двух чипов используется только если `GET /api/patterns` пуст.

- **Контракт выхода трейлинг-стопа (задача #144, эпик #142)**: `config.trailing_stop` — верхнеуровневый блок exit-политики: его схема, дефолты и валидаторы лежат в `trading_config.py`. Формат: `{"enabled": bool, "steps": [{"trigger": 2.0, "stop": 1.9}, ...]}` — множители R от цены входа; **`take_partial` в контракте нет**, как и имён `trigger_r` / `lock_r`: частичный выход не входил в утверждённую сетку. `validate_trailing_steps()` не бросает исключений и возвращает стабильные коды причин `trailing_disabled` / `trailing_step_invalid` / `trailing_not_monotonic` / `trailing_too_many_steps` (пустой список = принято); `normalize_trailing_stop()` приводит блок к канону, а `resolve_trailing_stop(config)` отдаёт потребителю `{'enabled', 'steps', 'reasons'}`. Границы берутся из `TRAILING_STOP` и являются единственным источником истины: `0 < trigger <= 3.5`, `0 <= stop <= 3.0`, всегда `stop < trigger`, не больше `max_steps` = 6 ступеней, шаг — только dict (список пар не принимается). Минимального зазора между триггером и стопом нет намеренно: боевая лестница стоит на 0.1R, и правило «зазор ≥ 0.5R» отвергло бы дефолт. Отсутствие ключа, `None`, `{}` и не-dict-блок означают «трейлинга нет» с пустым списком причин, а `enabled=true` без пригодных шагов даёт код `trailing_disabled`. Флаг `enabled` строгий (лестницу включают только настоящий bool либо строки `'1' / 'true' / 'yes' / 'on'`), нормализация сохраняет точность float — 1.9 / 2.4 / 2.9 не округляются до 0.5R. Значения по умолчанию `TRAILING_STOP`: `enabled=false`, шаги `2.0→1.9 / 2.5→2.4 / 3.0→2.9` — сетка `ultra_late_tight`, утверждённая Product Owner 2026-09-08 (§18, handover §35) и поставляемая **выключенной**, поэтому ни одна конфигурация не меняет поведения. **Применяется с #145; гейта на записи по-прежнему нет:** `backend/app/analytics/trailing_stop.py` читает блок через `resolve_trailing_stop()`, и `StrategyEvaluator.on_bar`, плагин `levels_reversal`, `portfolio_simulator` и walk-forward вооружают лестницу из него (§19); `EXIT_TRAILING` эмитится начиная с #145. Движок не угадывает: лестницу, которую отверг валидатор, не вооружает вовсе, и такая позиция ведётся ровно как до #145. `validate_config()` в репозитории по-прежнему нет, поэтому битый блок продолжает сохраняться в `trading.strategies`, пока #146 / #149 не вызовут `require_valid_trailing_stop()` на пути записи; `paper_trader` и `live_executor` блок ещё не читают (#148, #151). Legacy-параметры паттернов `trailing_stop` / `trailing_step` в `pattern_registry.py` — другой контракт, #144 их не трогает. Тесты: `backend/tests/test_trailing_contract.py` (33 — контракт) и `backend/tests/test_trailing_stop.py` (52 — матрица поведения #145).

## 21. Эксплуатация превью паттерна на графике (эпик #87)

- Точка входа: `POST /api/patterns/preview` в `strategy_jobs.py`; логика в `app.analytics.pattern_preview`.
- Запрос: `ticker`, `pattern_id`, draft `params`, `date_from`, `date_to`. ТФ берётся из params (`level_timeframe` для `levels_reversal`, `timeframe` для id SignalEngine).
- Ответ: `status` (`ok` / `empty` / `error` / `unsupported`), `candles`, typed `overlays` (`ray`, `band`, `line`, `marker`). Задача #88 реализует `levels_reversal`: все уровни с `defined_ts` в окне; на каждый уровень — `ray` от `defined_ts` до последнего видимого бара и `band` зоны ATR. Не эмулировать лучи бесконечными price lines.
- Неизвестный `pattern_id` → `status=error` без 500. Нет свечей (в т.ч. неподдерживаемый `2h`) → `status=empty` с понятным сообщением.
- Остальные id паттернов → `status=unsupported` только со свечами, пока #91 не добавит renderer'ы. Frontend — #89–#92.
- Unit-тест: `cd backend && python -m pytest -q tests/test_pattern_preview.py`.

## 22. Эксплуатация state machine уровней

- Точки входа: `LevelsTracker` / `get_levels_with_state` / `is_broken` в `levels_engine.py`. Инициализация из `get_levels()` (алиас `build_levels`). Подавайте бары **того же** ТФ, что и уровни (обычно 4h). Только in-memory — без таблицы и миграции.
- Пороги только из `LEVEL_STATE_MACHINE` в `trading_config.py`: `breakout_buffer_atr=0.25`, `confirm_bars=2`, `min_penetration_atr=0.5`, `zone_extension_atr=0.5`. `zone_extension_atr` документирует текущую ширину зоны `build_levels`; трекер не пересчитывает `zone_lower`/`zone_upper`.
- Пробой сопротивления: последние `confirm_bars` close все выше `zone_upper`, последний close выше `zone_upper + buffer×ATR`, max(window) не ниже `zone_upper + min_penetration×ATR`. Поддержка симметрично ниже `zone_lower`. Первый close обратно внутри нативной зоны после пробоя: `broken_up → flipped_support` / `broken_down → flipped_resistance`. Ложный пробой в этой итерации не возвращается в `active`.
- `overlapping_resistance_zone_at` ветирует только `active` сопротивления, если есть колонка `state`, и пропускает `tracker.is_broken(level_id)`, если передан трекер (задача #107). Передавайте снимок трекера **после** `update()` только по закрытым HTF-барам. Кадры без `state` и вызовы без `tracker` сохраняют поведение задачи #97.
- `StrategyEvaluator` создаёт `LevelsTracker`, если в `config.patterns` есть `level_breakout_retest`, `levels_sr_breakout` или `levels_sr_support`. Locked `test_20260731` не включает ни один. `bars_since_breakout(level_id)` считает HTF-бары с момента подтверждённого пробоя.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_state_machine.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py`.

## 23. Эксплуатация паттерна Level Breakout Retest

- Точки входа: `check_breakout_retest` / `evaluate_level_breakout_retest` в `patterns/level_breakout_retest.py`; AND-фильтр `_check_level_breakout_retest` в `StrategyEvaluator`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/` (затенит `breakout.py`).
- Схема Lab: `PATTERN_REGISTRY['level_breakout_retest']` (также копируется в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для AC задачи #107 / `GET /api/patterns`). Дефолты: `level_timeframe=4h`, `retest_window_bars=20`, `retest_zone_atr=0.5`, `entry_trigger_bullish=true`, `stop_atr=1.0`, `risk_reward=2.0`. Порог бычьего тела `0.6` не настраивается в Lab; живёт в `LEVEL_BREAKOUT_RETEST` в `trading_config.py`.
- Критерии (все обязательны): состояние трекера `broken_up` или `flipped_support`; close в `[level ± retest_zone_atr×ATR]`; close ≥ пробитого `level_price`; `bars_since_breakout <= retest_window_bars`; если `entry_trigger_bullish` — `close > prev_high` ИЛИ бычье тело.
- Stop/take: `stop = entry − stop_atr×ATR`, `take = entry + risk_reward×(entry−stop)`. При включённом паттерне они заменяют levels stop/take; верхнеуровневый RR-фильтр конфига поверх не применяется (RR паттерна уже задаёт отношение).
- Контекст: `build_strategy_context` возвращает `htf_bars` (тот же ТФ, что и уровни). Evaluator подаёт в трекер только HTF-бары, чей close ≤ текущий 1min ts (без lookahead). Paper/live `load_context` / `update_context` прокидывают этот кадр. Lab/plugin-путь: `portfolio_backtest` кладёт тот же кадр в `MarketContext.htf_bars` (задача #116); на `candles_4h` не опираться.
- Взаимодействие с вето: при включённом паттерне пробитое сопротивление больше не opposing zone (`is_broken`). Без паттерна любое перекрывающееся сопротивление по-прежнему ветирует (locked `test_20260731`).
- Компонуемость: AND с `levels_reversal` (по-прежнему обязателен для пути зоны поддержки) и с фильтрами SignalEngine / `signal_4h_buy`. Чип Lab: handover §24. `GET /api/patterns` — источник имён, подсказок, иконки и схемы параметров.
- Locked `test_20260731` не перезаписывать.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_level_breakout_retest.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py`.

## 27. Эксплуатация AFKS smoke композита

- Пакет: `analytics/issue-119-afks-sr-breakout-smoke/`. Изолированный тикер, не слоты 50k.
- A = `levels_reversal` + `signal_4h_buy` (та же геометрия, что #103). B = только `levels_sr_breakout` + `signal_4h_buy`. Период `2024-08-01` … `timestamp < 2026-08-21`.
- Основной движок — `run_strategy_backtest`, чтобы на сделках остался `source`. Plugin-путь Lab после #116 совпадает по n/PF, но `source` с plugin-сделок сейчас теряется.
- B-support может быть больше A: композит передаёт `LevelsTracker` в вето, поэтому пробитое сопротивление больше не режет support-вход.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без БД: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py`. Полный пересчёт: `python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py`.
- Unit: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.

## 28. Эксплуатация Lab-вселенной композита A/B

- Пакет: `analytics/issue-124-sr-breakout-universe/`. Изолированная Lab-вселенная 28 тикеров (`get_big_tickers`), не live top-5 и не `run_params.tickers`.
- Конфиги A/B — опубликованная пара SHA из #119. Период `2024-08-01` … `timestamp < 2026-08-21`. Движок — `run_strategy_backtest`, чтобы на сделках остался `source`.
- Isolated B больше A по двум причинам: путь B (`levels_sr_breakout_resistance`) и extra support после вето с трекером. Не считать n support-пути B бит-в-бит копией A.
- Опциональный replay B на 50k / 10k / max 5 — **отдельный** блок. Не смешивать этот PF/equity с isolated PF по тикерам. Это не вердикт для paper.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без нового бэктеста: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Полный пересчёт: `python analytics/issue-124-sr-breakout-universe/extract_inputs.py` (резюмируется).
- Unit: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.

## 29. Эксплуатация паттерна «поддержка с трекером» (`levels_sr_support`)

- Точки входа: `PATTERN_ID` / `SOURCE` в `patterns/levels_sr_support.py`; только support-путь в `StrategyEvaluator._check_sr_support_entry`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/`.
- Схема Lab: `PATTERN_REGISTRY['levels_sr_support']` (также в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для `GET /api/patterns`). Категория `levels` (рядом с `levels_reversal` и `levels_sr_breakout`, не в breakout). Иконка `support_tracker` (должна отличаться от `breakout_up` и `support_breakout`). Параметры — **только** поля `levels_reversal`, без ключей ретеста. Чип Lab: handover §30. Не хардкодить ключи params в TSX.
- Изолированный прогон: в `config.patterns` есть `levels_sr_support` и опционально `signal_4h_buy` / id SignalEngine. `levels_reversal` **не** обязателен. `run_strategy_backtest` считает этот id достаточным движком входа.
- Порядок в `check_entry` после сессии / HTF / `_sync_tracker`: (1) если включён `levels_sr_breakout` — побеждает композит (без изменений); (2) иначе если включён `levels_sr_support` — общие AND, затем зона поддержки + confirm + вето *активного* сопротивления с `tracker=self._tracker` → `source=levels_sr_support`, levels stop/take, верхнеуровневый RR. **Не** вызывать `check_breakout_retest` на этом id.
- Оба чипа (`levels_sr_support` + `levels_sr_breakout`): побеждает композит. `levels_sr_support` + `levels_reversal`: побеждает новый id (один support-путь, без удвоения). Порядок `_LEVELS_CONFIRM_PATTERN_IDS`: композит > поддержка-с-трекером > `levels_reversal`.
- Отличие от `levels_reversal`: трекер передаётся в вето, пробитое сопротивление больше не режет валидный support-вход. Отличие от `levels_sr_breakout`: нет пути B / ретеста.
- Трекер / `htf_bars`: та же подача, что #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit-тесты не зависят от Lab UI.
- Locked `test_20260731` этот id не включает (paper/live вето и levels stop/take остаются бит-в-бит).
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_levels_sr_breakout.py tests/test_strategy_plugin.py`.
