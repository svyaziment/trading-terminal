# Инструкции команды · Trading Terminal

Эпик в работе: **#11 — Конфигурируемые паттерны** (задачи #12–#15).

| Документ | Зачем |
|---|---|
| [WORKFLOW.md](WORKFLOW.md) | Регламент: как взять задачу, ветвиться, отчитываться, проходить review |
| [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) | Обязательная структура `report.json` + `log.txt` (+ доп. артефакты) |
| [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) | Чек-лист Team Lead при approve |

## Задачи эпика #11
| Issue | Задача | Ветка | Зависит от |
|---|---|---|---|
| #12 | Pattern registry + формат конфига | `feature/pattern-registry` | — |
| #13 | Агрегация 1W/1M свечей | `feature/candles-1w-1m` | — |
| #14 | Параметризованный движок + контекст (+регрессия) | `feature/parameterized-engine` | #12 |
| #15 | Универсальная модалка настроек | `feature/pattern-settings-modal` | #12, #14 |

**Порядок:** `#12` ∥ `#13` (параллельно) → `#14` → `#15`.
