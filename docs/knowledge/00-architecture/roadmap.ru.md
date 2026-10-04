# Статус roadmap

> **Source:** project-context.ru.md sections 8
> **Last refreshed:** 2026-10-04, task-346

## 8. Статус roadmap

| Блок | Описание | Статус |
|---|---|---|
| A | Базовая инфраструктура (FastAPI, DB, T-Bank API) | Готово |
| B | Индикаторы (33) | Готово |
| C | Паттерны (10) + сигналы | Готово |
| D | Frontend (SignalsPanel, PipelineWidget) | Готово |
| E | Background jobs (refresh, regenerate, shared lock) | Готово |
| F | Документация (project-context, handover, policy) | Готово (этот refresh) |
| G | Backtest engine + pattern matrix | Готово (legacy) |
| H | 1min свечи (MOEX ISS) + агрегация | Готово |
| K | Levels engine + levels backtest + matrix | Готово |
| L | Strategy Lab (параметризуемый движок + walk-forward + хранение + UI) | Готово |
| M | Paper trading (параметризованная стратегия из Strategy Lab, один arm market) | Готово (верифицировано: 72 сигнала, 62 позиции) |
| N | Trading universe (top-15 по PF, единый источник истины) | Готово |
| I | ML (CatBoost/LightGBM) | Не начато |
| J | Отчёт анализа A/B теста (signal_source x window x rr x entry) | Ожидает (накопить закрытые сделки) |
| O | Strategy Plugin System (StrategyPlugin ABC + registry + portfolio simulator) | Готово (Эпик #39) |
| P | Live Trading Infrastructure (sandbox-исполнение, рыночные фильтры, риск-контроль, alerting, панель управления) | Backend-исполнение #59-#62, Telegram #64, monitoring panel #65, live-вселенная #66, логи отказов #73 и первая sandbox canary #74 готовы |
| Q | Паттерны SignalEngine в Strategy Lab (эпик #78) | #79–#82 готовы (evaluator, схемы registry, E2E/docs, группировка UI Lab) |
| R | Превью паттерна на графике Lab + Сигналы (эпик #87) | #88 API preview + оверлеи levels готовы; #89–#92 далее |
| S | Пробой уровня и смена роли (эпик #105) | #106 LevelsTracker + #107 `level_breakout_retest` AND-фильтр + #109 чип Lab готовы; аналитическая валидация и опциональное превью далее |
| T | Композитный S/R паттерн (эпик #115) | #116 Lab/plugin HTF + JSONB Infinity + #117 `levels_sr_breakout` + #118 чип Lab + #119 AFKS smoke + #124 Lab-вселенная A/B готово |
| U | Поддержка с трекером (эпик #126) | #127 backend `levels_sr_support` + #128 чип Lab + #129 isolated Lab-вселенная + #130 портфель #44 готово |
| V | Аналитика ступенчатого трейлинг-стопа (задача #139) | Готово — аналитический A/B (фикс. 1:3 против ступенчатого трейлинга) на locked `test_20260830_new_level` id=126, RR 1:3; трейлинг в `analytics/.../trailing.py`, в боевой путь выхода не входит |
| W | Пошаговый трейлинг-стоп в продакшене (Эпик #142) | Контракт #144 готов, движок #145 готов, редактор Lab #146 готов, интеграция в paper trading #148 выполнена. Gate паритета #147 эскалирован, sandbox live #151 в ожидании. |
