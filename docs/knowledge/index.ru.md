# Торговый терминал — индекс знаний

> **Last refreshed:** 2026-10-04, task-346
> **Статус:** актуально
> **Source:** docs/ (26 пар EN+RU, монолиты `docs/agents/` → `docs/knowledge/`)

## Карта за 30 секунд

**Торговый терминал** — система paper trading по акциям MOEX (без реальной торговли).
**Стек:** FastAPI (Python 3.12), React, PostgreSQL. **Данные:** T-Bank Invest API (gRPC, песочница) + MOEX ISS API (REST, минутные свечи).
**Три опоры:** бэктест / Strategy Lab, paper trading, live-исполнение в песочнице.

## Быстрая навигация

| Вопрос | Файл |
|---|---|
| Как устроена система? | `00-architecture/overview.md` |
| Структура проекта / стек | `00-architecture/file-structure.md` |
| API-эндпоинты | `00-architecture/api-endpoints.md` |
| Известные проблемы | `00-architecture/known-issues.md` |
| Что запланировано? | `00-architecture/roadmap.md` |
| Операционные заметки | `00-architecture/important-notes.md` |
| Потоки свечей (MOEX ISS) | `01-data-pipeline/candle-pipeline.md` |
| Общий конвейер данных | `01-data-pipeline/pipeline.md` |
| Стриминг брокера (T-Bank) | `01-data-pipeline/broker-streaming.md` |
| Схема БД и миграции | `02-database/schema.md` |
| Pattern-фичи (сигналы) | `03-strategies/pattern-features.md` |
| Strategy Lab | `03-strategies/strategy-lab.md` |
| UI Strategy Lab | `03-strategies/strategy-lab-ui.md` |
| Торговый юниверс (тикеры) | `03-strategies/trading-universe.md` |
| Трейлинг-стоп (ступени, ratchet, S/R) | `03-strategies/trailing-stop.md` |
| Live executor | `04-execution/live-executor.md` |
| Брокерный слой | `04-execution/broker-layer.md` |
| Risk-гейты | `05-risk-management/risk-gates.md` |
| Equity-правила / sizing | `05-risk-management/equity-rules.md` |
| Политика секретов | `05-risk-management/secrets-policy.md` |
| Дашборды фронтенда | `06-frontend/dashboard.md` |
| Runbook (запуск / остановка / проверка) | `07-operations/runbook.md` |
| Сессия торгов MOEX | `07-operations/moex-session.md` |
| Алерты | `07-operations/alerts.md` |
| Live-телеметрия | `07-operations/live-telemetry.md` |
| Брокер-операции | `07-operations/broker-ops.md` |
| Решения-канарии (ADR) | `08-decisions/canary.md` |
| Эпики #142 / #172 / #190, закрытые аналитика | `09-epics-history/epics-history.md` |

## Разделы

| # | Раздел | Что внутри |
|---|---|---|
| 00 | architecture | обзор, стек, карта файлов, API, проблемы, roadmap |
| 01 | data-pipeline | свечи, MOEX ISS, стриминг брокера, конвейер |
| 02 | database | схема trading, миграции |
| 03 | strategies | паттерн-сигналы, Strategy Lab, юниверс, трейлинг-стоп |
| 04 | execution | live executor, брокерный слой |
| 05 | risk-management | риск-гейты, equity-правила, секреты |
| 06 | frontend | дашборды и панели |
| 07 | operations | runbooks, сессия MOEX, алерты, телеметрия, брокер-операции |
| 08 | decisions | ADR, канарии |
| 09 | epics-history | #142 / #172 / #190, закрытые аналитические пакеты |

## SOP и правила

- **SOP:** `.clinerules/developer-sop.ru.md` — закон проекта для всех агентов.
- **Политика документации:** `docs/agents/documentation-policy.md`.
- **Красные линии:** locked-стратегии (126 / 36 / 102 / 118) и закрытые `analytics/` — только ссылки; `allow_real_trading` никогда не включать; пары EN+RU обновляются вместе.
- **`reports/`** — рабочие документы, не коммитятся (папки эпиков/Issue хранятся локально).

## Перекрёстные ссылки со старых путей

- `docs/agents/project-context.md` → stub (перенесено в `docs/knowledge/`, таблица внутри stuba).
- `docs/agents/handover.md` → stub (перенесено в `docs/knowledge/`, таблица внутри stuba).
- `docs/knowledge/glossary.ru.md` / `CHANGELOG.ru.md` — термины и история изменений.

## Связанные

- `docs/knowledge/09-epics-history/epics-history.ru.md` — исторические волны работ.
- `workspace/context/docs-tree-before.txt` — снимок дерева до миграции.
