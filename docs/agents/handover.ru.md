# Руководство по передаче контекста агента: Trading Terminal

> **Статус:** заглушка — перенесено в `docs/knowledge/`.
> Перенесено в `docs/knowledge/` (см. [`docs/knowledge/index.ru.md`](../knowledge/index.ru.md) — единственная точка входа).
> Исходный текст сохранён в файлах `.bak` ниже.
> **Последнее обновление:** 2026-10-04, task-346

Используйте таблицу ниже, чтобы найти, где теперь находится каждая из бывших секций. Она содержит все **61** перенесённые секции — `pc §1`–`pc §24` из этого монолита и `ho §10`–`ho §46` из [handover.ru.md](handover.ru.md) (`ho §1`–`ho §9` — заглушки, дублирующие `pc §1`–`pc §9`; опущены). `pc` = project-context, `ho` = handover.

## Section navigation — 61 sections

| Old section | Old title | New location |
|---|---|---|
| pc §1 | Обзор проекта | [`overview.ru.md`](../knowledge/00-architecture/overview.ru.md) |
| pc §2 | Структура файлов | [`file-structure.ru.md`](../knowledge/00-architecture/file-structure.ru.md) |
| pc §3 | Схема базы данных (PostgreSQL, схема: trading) | [`schema.ru.md`](../knowledge/02-database/schema.ru.md) |
| pc §4 | Конвейер данных | [`pipeline.ru.md`](../knowledge/01-data-pipeline/pipeline.ru.md) |
| pc §5 | API Endpoints | [`api-endpoints.ru.md`](../knowledge/00-architecture/api-endpoints.ru.md) |
| pc §6 | Паттерны (10 всего) | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| pc §7 | Известные проблемы и статус | [`known-issues.ru.md`](../knowledge/00-architecture/known-issues.ru.md) |
| pc §8 | Статус roadmap | [`roadmap.ru.md`](../knowledge/00-architecture/roadmap.ru.md) |
| pc §9 | Важные замечания | [`important-notes.ru.md`](../knowledge/00-architecture/important-notes.ru.md) |
| pc §10 | Интеграция T-Bank Sandbox API | [`broker-streaming.ru.md`](../knowledge/01-data-pipeline/broker-streaming.ru.md) |
| pc §11 | Imbalance стакана в реальном времени | [`moex-session.ru.md`](../knowledge/07-operations/moex-session.ru.md) |
| pc §12 | Расчёт размера позиции | [`equity-rules.ru.md`](../knowledge/05-risk-management/equity-rules.ru.md) |
| pc §13 | Sandbox live executor | [`live-executor.ru.md`](../knowledge/04-execution/live-executor.ru.md) |
| pc §14 | Telegram alerting | [`alerts.ru.md`](../knowledge/07-operations/alerts.ru.md) |
| pc §15 | Панель мониторинга Live Trading | [`dashboard.ru.md`](../knowledge/06-frontend/dashboard.ru.md) |
| pc §16 | AND-фильтры SignalEngine в StrategyEvaluator | [`strategy-lab.ru.md`](../knowledge/03-strategies/strategy-lab.ru.md) |
| pc §17 | Аналитика ступенчатого трейлинг-стопа (задача #139) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| pc §18 | Аналитика устойчивости сетки трейлинг-стопа (задача #143, форма решётки — #155) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| pc §19 | Ступенчатый трейлинг-стоп в боевом пути (задача #145, эпик #142) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| pc §20 | Редактор трейлинг-стопа в Strategy Lab (задача #146, эпик #142) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| pc §21 | Live trailing stop (Задача #151) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| pc §22 | Live equity и риск-гейты (задача #176, эпик #172, блок D) | [`risk-gates.ru.md`](../knowledge/05-risk-management/risk-gates.ru.md) |
| pc §23 | Telegram-алертинг и метрики live-контура (задача #177, эпик #172, блок E) | [`live-telemetry.ru.md`](../knowledge/07-operations/live-telemetry.ru.md) |
| pc §24 | Брокерский слой: песочница и реальный контур (задача #178, эпик #172, блок F) | [`broker-layer.ru.md`](../knowledge/04-execution/broker-layer.ru.md) |
| ho §10 | Операционные тонкости | [`runbook.ru.md`](../knowledge/07-operations/runbook.ru.md) |
| ho §11 | Протокол сотрудничества (агенты) | [`runbook.ru.md`](../knowledge/07-operations/runbook.ru.md) |
| ho §12 | Эксплуатация фильтра imbalance стакана | [`moex-session.ru.md`](../knowledge/07-operations/moex-session.ru.md) |
| ho §13 | Работа с клиентом T-Bank Sandbox | [`broker-ops.ru.md`](../knowledge/07-operations/broker-ops.ru.md) |
| ho §14 | Эксплуатация расчёта размера позиции | [`equity-rules.ru.md`](../knowledge/05-risk-management/equity-rules.ru.md) |
| ho §15 | Эксплуатация sandbox live executor | [`live-executor.ru.md`](../knowledge/04-execution/live-executor.ru.md) |
| ho §16 | Эксплуатация Telegram alerts для paper trading | [`alerts.ru.md`](../knowledge/07-operations/alerts.ru.md) |
| ho §17 | Эксплуатация панели Live Trading | [`dashboard.ru.md`](../knowledge/06-frontend/dashboard.ru.md) |
| ho §18 | Эксплуатация live-вселенной | [`trading-universe.ru.md`](../knowledge/03-strategies/trading-universe.ru.md) |
| ho §19 | Первая canary-сессия sandbox LiveExecutor | [`canary.ru.md`](../knowledge/08-decisions/canary.ru.md) |
| ho §20 | Эксплуатация фильтров SignalEngine в Strategy Lab | [`strategy-lab.ru.md`](../knowledge/03-strategies/strategy-lab.ru.md) |
| ho §21 | Эксплуатация превью паттерна на графике (эпик #87) | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §22 | Эксплуатация state machine уровней | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §23 | Эксплуатация паттерна Level Breakout Retest | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §24 | Эксплуатация чипа Level Breakout Retest в Lab | [`strategy-lab-ui.ru.md`](../knowledge/03-strategies/strategy-lab-ui.ru.md) |
| ho §25 | Эксплуатация композитного S/R паттерна (`levels_sr_breakout`) | [`candle-pipeline.ru.md`](../knowledge/01-data-pipeline/candle-pipeline.ru.md) |
| ho §26 | Эксплуатация чипа композитного S/R в Lab | [`strategy-lab-ui.ru.md`](../knowledge/03-strategies/strategy-lab-ui.ru.md) |
| ho §27 | Эксплуатация AFKS smoke композита | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §28 | Эксплуатация Lab-вселенной композита A/B | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §29 | Эксплуатация паттерна «поддержка с трекером» (`levels_sr_support`) | [`pattern-features.ru.md`](../knowledge/03-strategies/pattern-features.ru.md) |
| ho §30 | Эксплуатация чипа «поддержка с трекером» в Lab | [`strategy-lab-ui.ru.md`](../knowledge/03-strategies/strategy-lab-ui.ru.md) |
| ho §31 | Эксплуатация isolated-вселенной поддержки с трекером | [`trading-universe.ru.md`](../knowledge/03-strategies/trading-universe.ru.md) |
| ho §32 | Эксплуатация портфеля поддержки с трекером | [`trading-universe.ru.md`](../knowledge/03-strategies/trading-universe.ru.md) |
| ho §33 | Эксплуатация sandbox LiveExecutor на `test_20260830_new_level` (задача #135) | [`live-executor.ru.md`](../knowledge/04-execution/live-executor.ru.md) |
| ho §34 | Аналитика ступенчатого трейлинг-стопа на `test_20260830_new_level` (задача #139) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §35 | Аналитика устойчивости сетки трейлинг-стопа на `test_20260830_new_level` (задача #143, решётка v3 из #155) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §36 | Эксплуатация контракта конфигурации трейлинг-стопа (задача #144) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §37 | Эксплуатация боевого трейлинг-стопа (задача #145) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §38 | Эксплуатация редактора трейлинг-стопа в Lab (задача #146) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §39 | API-интеграция трейлинг-стопа (задача #149) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §40 | Трейлинг-метрики в панелях Paper / Live (задача #150) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §41 | Эксплуатация live trailing stop (задача #151) | [`trailing-stop.ru.md`](../knowledge/03-strategies/trailing-stop.ru.md) |
| ho §42 | Контракт схемы live_positions и fail-fast preflight (задача #173) | [`schema.ru.md`](../knowledge/02-database/schema.ru.md) |
| ho §43 | Эксплуатация живучести LiveExecutor (задача #174) | [`live-executor.ru.md`](../knowledge/04-execution/live-executor.ru.md) |
| ho §44 | Эксплуатация риск-гейтов live equity (задача #176) | [`risk-gates.ru.md`](../knowledge/05-risk-management/risk-gates.ru.md) |
| ho §45 | Эксплуатация Telegram-алертинга и мониторинга live-контура (задача #177) | [`live-telemetry.ru.md`](../knowledge/07-operations/live-telemetry.ru.md) |
| ho §46 | Реальный контур T-Bank, деплой-миграции и глобальный аварийный останов (задача #178) | [`broker-layer.ru.md`](../knowledge/04-execution/broker-layer.ru.md) |

---

## Full backups (safety nets)
- Original EN: [`project-context.md.bak`](project-context.md.bak)
- Original RU: [`project-context.ru.md.bak`](project-context.ru.md.bak)
- Handover EN: [`handover.md.bak`](handover.md.bak)
- Handover RU: [`handover.ru.md.bak`](handover.ru.md.bak)

## Entry point
- Index (single entry): [`docs/knowledge/index.ru.md`](../knowledge/index.ru.md)
