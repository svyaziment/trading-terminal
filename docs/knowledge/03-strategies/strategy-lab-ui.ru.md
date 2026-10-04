# Эксплуатация чипа Level Breakout Retest в Lab

> **Source:** handover.ru.md sections 24, 26, 30
> **Last refreshed:** 2026-10-04, task-346

## 24. Эксплуатация чипа Level Breakout Retest в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts`, `patternValidation.ts`.
- Включается в группе **Пробой**. Видимое имя — API `label` («Пробой уровня с ретестом»); EN `label_en` («Level Breakout Retest») в tooltip и под заголовком модалки. Иконка `breakout_up` (стрелка через уровень) тоже из API.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить шесть параметров во frontend. Схема: `level_timeframe` (1h/4h/1d), `retest_window_bars` (1–100), `retest_zone_atr` (0.1–2.0), `entry_trigger_bullish`, `stop_atr` (0.5–3.0), `risk_reward` (≥1). Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Комбинировать с `levels_reversal` (по-прежнему нужен для пути зоны поддержки) и опциональными фильтрами SignalEngine / `signal_4h_buy`. Логика AND не меняется. Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: после подтверждённого пробоя сопротивления нужен вход на ретесте (смена роли), а не только от нативной зоны поддержки. На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Этот AND-фильтр **не** замена `levels_sr_breakout` (handover §25). Эпик #115 испытывает композит изолированно; не комбинировать два чипа как «новую стратегию».

## 26. Эксплуатация чипа композитного S/R в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts` (`resolveConfirmWindows`), `patternValidation.ts`. Карта иконок: `PatternIcon.tsx` по API `icon`, не по id паттерна.
- Включается в группе **Уровни** (не **Пробой**). Видимое имя — API `label` («Поддержка + пробой сопротивления»); EN `label_en` («Support Reversal + Resistance Breakout») в tooltip и под заголовком модалки. Иконка `support_breakout` (линия поддержки + пробой сопротивления) тоже из API и должна отличаться от `breakout_up`.
- Этот чип **заменяет** `levels_reversal` для новой стратегии. Изолированный прогон: включить `levels_sr_breakout` и опционально `signal_4h_buy` / SignalEngine; `levels_reversal` и `level_breakout_retest` оставить выключенными. Если оба levels-чипа включены, на backend побеждает композит (один support-путь) — это не третий AND.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить список параметров во frontend. Схема = все поля `levels_reversal` + поля ретеста. Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Верхнеуровневый `config.confirm_windows` берётся из включённой схемы, у которой есть этот param; композит побеждает `levels_reversal` (как backend `_LEVELS_CONFIRM_PATTERN_IDS`). Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: нужен один Lab-движок, который входит и от нативной поддержки, и на подтверждённом ретесте сопротивления. На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Изолированный AFKS smoke (задача #119): handover §27. Lab-вселенная A/B (задача #124): handover §28. Движок только поддержки (задача #127): handover §29. Чип Lab (задача #128): handover §30. Isolated support-вселенная (задача #129): handover §31. Портфель 50k (задача #130): handover §32. Не считать ни один пакет вердиктом для paper.

## 30. Эксплуатация чипа «поддержка с трекером» в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts` (`resolveConfirmWindows`, `LEVELS_CONFIRM_PATTERN_IDS`), `patternValidation.ts`. Карта иконок: `PatternIcon.tsx` по API `icon`, не по id паттерна.
- Включается в группе **Уровни** (не **Пробой**). Видимое имя — API `label` («Поддержка с трекером»); EN `label_en` («Support Reversal (tracker veto)») в tooltip и под заголовком модалки. Иконка `support_tracker` (линия поддержки + трекер в зоне, без стрелки пробоя) тоже из API и должна отличаться от `support_breakout` и `breakout_up`.
- Этот чип **заменяет** `levels_reversal` / `levels_sr_breakout` для этой стратегии. Изолированный прогон: включить `levels_sr_support` и опционально `signal_4h_buy` / SignalEngine; `levels_reversal`, `levels_sr_breakout` и `level_breakout_retest` оставить выключенными. Если одновременно включён `levels_sr_breakout` — на backend побеждает композит. Если одновременно `levels_sr_support` и `levels_reversal` — побеждает новый id (один support-путь) — это не третий AND.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить список параметров во frontend. Схема = только поля `levels_reversal`, без ключей ретеста. Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Верхнеуровневый `config.confirm_windows` берётся из включённой схемы, у которой есть этот param; приоритет композит > поддержка-с-трекером > `levels_reversal` (как backend `_LEVELS_CONFIRM_PATTERN_IDS`). Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: нужен путь B-support из #124 (вето с трекером, без ретеста сопротивления). На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated vs #124 B-support — задача #129 (handover §31). Портфель 50k — задача #130 (handover §32). Не считать этот чип вердиктом для paper.
