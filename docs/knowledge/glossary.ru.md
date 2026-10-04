# Глоссарий

> **Source:** документация проекта (task-346)
> **Last refreshed:** 2026-10-04, task-346
> **Связи:** `00-architecture/overview.md`, `03-strategies/strategy-lab.md`, `07-operations/runbook.md`

| Термин | Определение | Где читать |
|---|---|---|
| Торговый терминал | Система paper trading по акциям MOEX (без реальной торговли). Стек: FastAPI (Python 3.12) + React + PostgreSQL. | `00-architecture/overview.md` |
| Paper trading | Симуляция торговли без реальных денег: позиции и ордера живут в локальной БД. | `00-architecture/overview.md` |
| Песочница (sandbox) | Режим T-Bank Invest API без реальных средств; весь терминал — песочница. | `01-data-pipeline/broker-streaming.md` |
| Backtest (бэктест) | Историческая прогонка стратегии по свечам для оценки PnL / MaxDD. | `03-strategies/strategy-lab.md` |
| Strategy Lab (лаборатория стратегий) | Модуль создания, тестирования и запуска стратегий. | `03-strategies/strategy-lab.md` |
| Locked-стратегия | Стратегия с `locked=true` (126 / 36 / 102 / 118); контент неизменен, только ссылки. | `03-strategies/trading-universe.md` |
| Trading universe (торговый юниверс) | Набор торгуемых тикеров MOEX; управляется через `trading_config.py` (без хардкода тикеров). | `03-strategies/trading-universe.md` |
| Паттерн / pattern features | Сигналы входа по форме свечи (напр. `ultra_late_tight`); фичи считаются по свечам. | `03-strategies/pattern-features.md` |
| Трейлинг-стоп | Закрытый ордер, идущий за ценой по ступеням, защищает нереализованную прибыль. | `03-strategies/trailing-stop.md` |
| Уровень / ступень | Одна из дискретных ступеней трейлинга; стоп сдвигается ступенями, а не непрерывно. | `03-strategies/trailing-stop.md` |
| Ratchet (ракетное поведение) | Однонаправленное поведение: стоп двигается только в благоприятную сторону, назад не откатывается. | `03-strategies/trailing-stop.md` |
| Уровни S / R | Стоп (S) и тейк-профит (R) позиции. | `03-strategies/trailing-stop.md` |
| Risk gate (риск-гейт) | Проверка, которую должен пройти ордер/действие (лимиты, экспозиция, состояние). | `05-risk-management/risk-gates.md` |
| Equity rules | Правила sizing и лимитов портфеля по текущему equity. | `05-risk-management/equity-rules.md` |
| Live executor | Компонент, превращающий сигналы стратегии в paper-ордера через брокерный слой. | `04-execution/live-executor.md` |
| Брокерный слой | Абстракция над T-Bank Invest API (gRPC): ордера, позиции, состояние счёта. | `04-execution/broker-layer.md` |
| Slot (слот) | временной бакет в пайплайне исполнения/телеметрии (один слот — один тик обработки). | `07-operations/live-telemetry.md` |
| SignalEngine | Компонент генерации сигналов входа из паттернов + фильтров. | `03-strategies/pattern-features.md` |
| AND-фильтр | Многоусловный фильтр: все условия должны выполняться, чтобы сработал сигнал. | `03-strategies/pattern-features.md` |
| Walk-forward | Вариант бэктеста с повторной валидацией стратегии по скользящему вперёд окну. | `03-strategies/strategy-lab.md` |
| Свеча | Минутная свеча (MOEX ISS API) — базовая единица рыночных данных. | `01-data-pipeline/candle-pipeline.md` |
| PnL | Прибыль/убыток позиции или портфеля. | `02-database/schema.md` |
| MaxDD | Максимальный просад (drawdown) по кривой equity. | `03-strategies/strategy-lab.md` |
| T-Bank Invest API | Брокерский API (gRPC, sandbox) — ордера, позиции, котировки. | `01-data-pipeline/broker-streaming.md` |
| MOEX ISS API | API биржи (REST) — рыночные данные, минутные свечи. | `01-data-pipeline/candle-pipeline.md` |
| Runbook | Операционный playbook: запуск / остановка / проверка / восстановление сервисов. | `07-operations/runbook.md` |
