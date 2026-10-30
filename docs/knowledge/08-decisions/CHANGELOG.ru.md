# Журнал решений и изменений (RU)

> **Раздел:** 08-decisions — журнал изменений базы знаний.
> **Последнее обновление:** 2026-10-29, task-216

| Дата | Задача | Раздел | Изменение | Исполнитель |
|------|--------|--------|-----------|-------------|
| 2026-10-29 | #216 | 02-database/schema.ru.md, 04-execution/live-executor.ru.md, 07-operations/runbook.ru.md | Переход на реальные торги: единый раздел реального контура исполнителя (разделение процессов, двухфазное чтение kill switch, `PostgresLogHandler` → `trading.live_executor_events`), переписан runbook «Операции живой торговли», новая таблица `trading.live_executor_events` в schema | Arctic |
