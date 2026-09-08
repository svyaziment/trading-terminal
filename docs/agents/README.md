# Инструкции команды · Trading Terminal

Эпик в работе: **#142 — Ступенчатый трейлинг-стоп в боевом пути** (задачи #143–#152 плюс follow-up #155). Основание — аналитика #139 (PR #141): на запертой `test_20260830_new_level` ступенчатый трейлинг даёт +8.40% капитала, PF 1.41 → 1.54 и daily MaxDD 6.49% → 2.74%. Механизм ставится **выключенным по умолчанию** (`config.trailing_stop.enabled=false`).

| Документ | Зачем |
|---|---|
| [WORKFLOW.md](WORKFLOW.md) | Регламент: как взять задачу, ветвиться, отчитываться, проходить review |
| [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) | Обязательная структура `report.json` + `log.txt` (+ доп. артефакты) |
| [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) | Чек-лист Team Lead при approve |

## Задачи эпика #142

| Issue | Задача | Исполнитель | Зависит от |
|---|---|---|---|
| #144 | Контракт `config.trailing_stop`: схема, нормализация, валидация, `EXIT_TRAILING` | Arctic (Backend) | — |
| #145 | Ступенчатый трейлинг в `StrategyEvaluator` + плагин + `portfolio_simulator` (регрессия bit-for-bit) | Arctic (Backend) | #144 |
| #146 | Редактор ступеней трейлинга в Strategy Lab (schema-driven) | Fennec (Frontend) | #144 |
| #147 | Паритет боевого пути с книгой B из #139 — **merge-гейт волны 1** | Vulpec (Analytics) | #145 |
| #143 | Робастность: сетки ступеней, walk-forward по периодам, slippage/комиссии | Vulpec (Analytics) | #145 |
| #155 | Расшишение решётки #143 до `143-trailing-v3`: 8 сеток (две одношаговые, безубыточная граница и PO-проба), машиночитаемый `summary.json.lattice`, тесты `backend/tests/test_issue155_analysis.py` | Vulpec (Analytics) | #143 |
| #148 | Трейлинг в paper-контуре: миграция, `monitor_open`, catchup, Telegram | Arctic (Backend) | #145, #147 |
| #149 | API и фильтры: strategies / paper-trading / live-trading, `closed_trailing` | Arctic (Backend) | #148 |
| #150 | Текущий стоп и выход `trailing` в панелях Paper Trading / Live Trading | Fennec (Frontend) | #149 |
| #151 | Трейлинг в песочнице `LiveExecutor`: ratchet синтетического стопа, рестарт, kill-switch | Arctic (Backend) | #148, #149 |
| #152 | Приёмка на живом периоде: фактические закрытия против модели, вердикт leave/tune/rollback | Vulpec (Analytics) | #151 |

**Порядок:** `#144` → (`#145` ∥ `#146`) → (`#147` ∥ `#143`) → `#148` → (`#149` → `#150`; `#151`) → `#152`.
**Критический путь (Arctic):** `#144` → `#145` → `#148` → `#151`.

Красные линии эпика: трейлинг выключен по умолчанию и не меняет поведение при `enabled=false`; ступени только в R от входа; запертые `test_20260830_new_level` (126) и референсные 36 / 102 / 118 не трогаем; пакет `analytics/issue-139-trailing-stop-new-level/` не рефакторим; документация — EN и RU в одном PR.

Ветки: `feature/issue-<номер>-<slug>`. Отчёты: `reports/<Роль>/<номер>_<slug>/`.

Исторические эпики (#11 конфигурируемые паттерны, #39 плагины, #78 паттерны SignalEngine, #87 превью паттернов, #105 пробой уровня, #115 композитный S/R, #126 поддержка с трекером) — закрыты; статус блоков см. roadmap в [project-context.md](project-context.md) §8.

