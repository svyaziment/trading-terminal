# Epic #001: Production T-Bank live trading readiness

**Status:** in-progress
**Created:** 2026-09-18
**Last refreshed:** 2026-09-18

## Summary

Эпик приводит live-контур исполнителя (`backend/app/analytics/live_executor.py`) в состояние, пригодное для торговли реальными деньгами через T-Bank Invest API: устраняет дрейф схемы `trading.live_positions`, делает главный цикл отказоустойчивым, а shutdown — безопасным, переносит защиту позиций на сторону брокера (`STOP_LOSS` + трейлинг через amend стоп-ордера), добавляет учёт live equity с риск-гейтами, уведомления/мониторинг и явный ограждённый переключатель на реальный счёт.

Текущее состояние (проверено по диску, тестам и живой БД 2026-09-18): исполнение возможно **только против T-Bank Sandbox**; take-profit выставляется реальным лимитным ордером (`live_executor.py:787-811`), а стоп-лосс — **синтетический**: триггер по опросу раз в `check_interval_seconds=30` и исполнение *лимитным* ордером по текущей цене (`:953-988`) без защитных тиков. Аварийный останов снимает защиту с позиций, риск-лимиты не применяются, уведомления отсутствуют, учёт проскальзывания не заполняется.

## Scope

### IN
- Выравнивание runtime-DDL `ensure_live_positions_table` с миграцией `20260916_001` (30 колонок, CHECK из 7 статусов) и fail-fast проверка схемы в preflight.
- Живучесть главного цикла: `try/except` + backoff вокруг итераций и каждой позиции; инвариант «нет открытой позиции без брокерского стоп-ордера»; безопасный `shutdown()`; корректный advisory lock 151001 на выделенном соединении.
- Брокерская защита: `post_stop_order` / `cancel_stop_order` / `get_stop_orders` / `get_operations` в клиенте; постановка `STOP_LOSS` сразу после входа; трейлинг как атомарный amend (cancel+post); применение `trailing_protective_ticks`; реконсиляция по реальным филлам (`exit_price_actual`, `lots_executed`, `slippage_bp`, `slippage_r`).
- Live equity и риск-гейты: таблица `trading.live_equity`, snapshot equity каждый цикл, блокировка новых входов при drawdown >= `max_daily_loss_pct`, лимит нотила позиции через `max_position_size`, env-переопределения `RiskConfig`.
- Уведомления и мониторинг: интеграция `TelegramNotifier` (open/close/trailing/protection-failure/kill-switch/risk-breach), heartbeat + watchdog, метрики в `api/live_trading_jobs.py`.
- Реальный контур исполнения: `backend/app/broker/tinkoff_live.py` (реальный `Client` с `INVEST_GRPC_API`), выбор брокера по конфигу при явном `allow_real_trading`, глобальный `live_trading_kill_switch`, `alembic upgrade head` как шаг деплоя, пополнение `.env.example`, обновление docs (RU/EN).

### OUT
- Изменения locked-стратегий, бэктест-контура и критериев сигналов.
- Шорт-позиции, мультивалютность, опционы/фьючерсы (только акции MOEX, long-only).
- Моделирование праздников MOEX (в v1 календарь не поддержан, `MOEX_SESSION` без holidays).
- Frontend-редизайн панелей; добавляются только новые метрики в существующие контракты API.
- Автоматический flatten при нарушении риск-лимита (решение PO: только блокировка входов + сохранение стопов).
- Перенос бэктеста на брокерские trailing-семантики.

## Architecture Decisions

- **Брокерский `STOP_LOSS` (рыночный по триггеру) — единственный источник защиты позиции; TP остаётся лимитным ордером; трейлинг выполняется как amend стоп-ордера.** Обоснование: синтетический триггер с опросом раз в 30 с и исполнением лимитным ордером по текущей цене оставляет позицию незащищённой при гэпе и при гибели процесса; `trailing_protective_ticks` сегодня только валидируется (`live_executor.py:274-276`) и нигде не применяется; рыночный по триггеру стоп гарантирует исполнение при гэпе. Альтернативы: `STOP_LIMIT` — отклонён из-за риска неисполнения в быстром рынке; полностью брокерский `TakeProfitType.TRAILING_STOP` — отклонён, так как семантика лестницы/шагов разойдётся с бэктестом и с текущими 68 тестами `backend/tests/test_live_executor.py`; OCO-эмуляция (`STOP_LOSS` + `TAKE_PROFIT` связанными ордерами) — отложена как более дорогая в реконсиляции, возможна отдельным follow-up.
- **Инвариант «нет открытой позиции без `broker_stop_id`»** проверяется каждый цикл; при нарушении — повторная постановка и `notify_critical`. Обоснование: `shutdown()` при `close_positions_on_shutdown=False` (дефолт, `trading_config.py:59`) сегодня отменяет TP и обнуляет `broker_stop_id`/`broker_take_id` (`live_executor.py:1253-1288`), оставляя позицию полностью без защиты.
- **Реконсиляция выходов по реальным филлам брокера, а не по модельным ценам.** Обоснование: все три вызова `_close_db_position` (`:924`, `:1223`, `:1274`) не передают `exit_price_actual`/`lots_executed`, поэтому `slippage_bp`/`slippage_r` всегда NULL, а причина выхода угадывается по наличию `broker_stop_id` (`:914`); сам учёт уже реализован в `:1090-1163`, но фактически мёртв.
- **Схема — через Alembic как источник истины; runtime-DDL остаётся идемпотентным суперсетом и дополняется fail-fast проверкой в preflight.** Обоснование: миграция `backend/alembic/versions/20260916_001_live_trailing_runtime.py` уже расширяет CHECK до 7 статусов и добавляет exit/slippage-колонки, но `ensure_live_positions_table` (`:180-219`) создаёт лишь 20 колонок и CHECK из 5 статусов → на свежей прод-БД трейлинг упадёт с `UndefinedColumn`, а запись `closed_trailing` нарушит CHECK. Альтернатива «только Alembic, runtime-DDL удалить» — отклонена, чтобы не сломать существующие тесты и автономный запуск исполнителя.
- **Advisory lock 151001 удерживается и освобождается на одном выделенном соединении.** Обоснование: `DBManager` использует `psycopg2.pool.ThreadedConnectionPool` с выдачей соединения на каждый вызов (`backend/app/db/db_manager.py:79-121`), поэтому `pg_try_advisory_lock` (в `initialize`) и `pg_advisory_unlock` (`:1293`) могут попасть в разные сессии → блокировка не освобождается либо дубликат исполнителя не блокируется.
- **Риск-гейты строятся на `trading.live_equity` по образцу `write_equity`** (`backend/app/analytics/paper_trader.py:464-516`): equity = cash + realized + unrealized из портфеля брокера, peak/drawdown, `notify_critical` при пересечении лимита. При breach — блокировка новых входов и гарантированное сохранение/довыставление стопов, **без** принудительного flatten (решение PO от 2026-09-18).
- **Реальный контур — отдельный класс-клиент с тем же duck-typed контрактом**, что и `TinkoffSandboxClient` (`execute_order`/`get_positions`/`cancel_order` + новые stop-методы); выбор клиента — по конфигу при явном `allow_real_trading=True` (сейчас `SANDBOX_TRADING.allow_real_trading=False`, `trading_config.py:117`). Обоснование: `_broker_call` вызывает `getattr(self.broker, method)` (`live_executor.py:284-304`), поэтому исполнитель не требует переделки под реальный счёт, а песочница остаётся контуром приёмки.
- **Миграции выполняются явно на деплое (`alembic upgrade head`)**, авто-миграцию на старте приложения не добавляем. Обоснование: `public.alembic_version` существует и отслеживается, а команда сегодня документирована только в `docs/agents/handover.md:811` и `handover.ru.md:818` — её нет ни в `docker-compose.yml`, ни в Dockerfile, ни в `start_processes.sh`.

## Constraints

- Locked-стратегии и бэктест-контур не изменяются; критерии сигналов остаются bit-for-bit.
- `api_rate_limit` остаётся в интервале (0, 10] (`_validate_config`, `live_executor.py:263-265`); семантика `blocking=False` и token-bucket сохраняются.
- Существующие 68 тестов `backend/tests/test_live_executor.py` должны оставаться зелёными; изменения поведения — только через явное расширение тестов.
- `close_positions_on_shutdown` по умолчанию остаётся `False`, но shutdown обязан оставлять позицию защищённой (стоп сохраняется либо позиция закрывается).
- Открытая позиция в боевой БД (на 2026-09-18: 1 `open`, 5 `closed_stop`, 2 `closed_take`) переносится отдельным явным шагом; автоматический flatten запрещён.
- Все временные файлы задачи — только в `reports/<NNN>-issue-<N>-<name>/`.

## Разбивка на Issues

Номера GitHub Issues присвоены при создании: epic-Issue **#172**, задачи A-F — **#173...#178** (полное соответствие — в разделе «Таблица соответствия Issues»).

### Задача A: Schema — выравнивание runtime-DDL и fail-fast preflight

- **Status:** planned
- **Goal:** на свежей БД `ensure_live_positions_table` создаёт полный набор из 30 колонок и CHECK из 7 статусов (`pending`, `open`, `closed_stop`, `closed_take`, `closed_trailing`, `closed_broker`, `cancelled`); preflight сверяет фактическую схему с требуемой и блокирует запуск с понятным сообщением.
- **Files:** `backend/app/analytics/live_executor.py` (`ensure_live_positions_table`, 180-219), `backend/app/analytics/live_executor_preflight.py`, `backend/tests/` (новый тест-модуль).
- **Depends on:** —
- **Unblocks:** B, C, D
- **Relevant docs:** `docs/agents/project-context.ru.md` (live-контур, схема), `handover.ru.md` (alembic-команды).
- **Constraints:** идемпотентность сохраняется (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS` + повторное создание CHECK — по образцу `20260916_001`); существующие данные не изменяются; downgrade-путь не ломается.
- **Acceptance:** тест на «пустой» БД создаёт таблицу и успешно пишет строку со статусом `closed_trailing` и трейлинг-полями; на искусственно узкой схеме preflight возвращает явную ошибку с перечнем недостающих колонок/статусов; 68 существующих тестов остаются зелёными.
- **Regression:** поведение на уже мигрированной БД (30 колонок) не меняется — DDL остаётся no-op.

### Задача B: Живучесть цикла, безопасный shutdown, корректный advisory lock

- **Status:** planned
- **Goal:** транзиентные ошибки БД/брокера не убивают процесс и не снимают защиту; shutdown всегда оставляет позицию защищённой; advisory lock 151001 берётся и освобождается на одном соединении.
- **Files:** `backend/app/analytics/live_executor.py` (`run` 1324-1405, `monitor_positions` 872-990, `_apply_trailing` 992-1089, `_close_db_position` 1090-1186, `refresh_contexts` 843-860, `process_latest_bars` 813-842, `shutdown` 1253-1296, `initialize`), `backend/app/db/db_manager.py` (выделенное соединение/контекст-менеджер), `backend/tests/`.
- **Depends on:** A
- **Unblocks:** C, D, E
- **Relevant docs:** `project-context.ru.md` (процессы и надзор), `handover.ru.md` (запуск `start_processes.sh`).
- **Constraints:** backoff ограничен и не ломает `check_interval_seconds`/`context_refresh_seconds`; `shutdown_requested` и SIGTERM/SIGINT-семантика сохраняются; дубликат исполнителя по-прежнему блокируется.
- **Acceptance:** инжектированные исключения в `monitor_positions`/`process_latest_bars`/`refresh_contexts` не прерывают цикл (логируются, растёт счётчик ошибок, после N подряд — критический алерт и остановка с сохранением защиты); при `close_positions_on_shutdown=False` после shutdown стоп-ордер остаётся у брокера; `pg_advisory_unlock` выполняется на том же соединении, что и lock (тест на фейковом пуле).
- **Regression:** сценарии `until_session_end`, `duration_minutes` и восстановление трейлинг-состояния при рестарте работают как раньше.

### Задача C: Брокерская защита — STOP_LOSS, amend-трейлинг, реконсиляция филлов

- **Status:** planned
- **Goal:** каждая открытая позиция защищена брокерским стоп-ордером с момента исполнения входа; трейлинг выполняется атомарным amend; выходы учитываются по реальным филлам.
- **Files:** `backend/app/broker/tinkoff_sandbox.py` (новые `post_stop_order`, `cancel_stop_order`, `get_stop_orders`, `get_operations`; контракты `PostStopOrderRequest`, `StopOrderDirection`, `StopOrderType`), `backend/app/analytics/live_executor.py` (`monitor_positions`, `_apply_trailing`, `_place_stop_order` (новый), `_close_db_position`, `process_signal`), `backend/app/analytics/trading_config.py` (новые ключи), `backend/tests/`.
- **Depends on:** B
- **Unblocks:** D, E, F
- **Relevant docs:** `project-context.ru.md` (live-исполнение), разделы по Issue #151 (трейлинг).
- **Constraints:** только `StopOrderType.STOP_LOSS` (рыночный по триггеру) для защиты; `trailing_protective_ticks` применяется к цене срабатывания/лимитной цене по семантике, зафиксированной в плане; amend = cancel+post с гарантией, что позиция не остаётся без стопа между вызовами; лимит API (0, 10] не превышается, при исчерпании token-bucket — отложить на следующую итерацию с алертом.
- **Acceptance:** sandbox-тесты на постановку/отмену/amend/частичное исполнение; после входа `broker_stop_id` заполнен; при сбое постановки — повтор + `notify_critical`; `exit_price_actual`, `lots_executed`, `slippage_bp`, `slippage_r` заполняются реальными филлами; инвариант «нет открытой позиции без стопа» проверяется каждый цикл; причина выхода определяется по факту исполнения, а не по наличию `broker_stop_id`.
- **Regression:** лестница трейлинга (`trailing_steps`, `step_reached`, `current_stop_price`) и статус `closed_trailing` сохраняют семантику Issue #151; панель Live Trading показывает те же поля.

### Задача D: Live equity и риск-гейты

- **Status:** planned
- **Goal:** каждый цикл пишется snapshot equity live-счёта; при превышении дневного/общего drawdown новые входы блокируются, а стопы сохраняются; размер позиции ограничен `max_position_size`; лимиты читаются из env.
- **Files:** `backend/app/analytics/live_executor.py` (новый `_write_live_equity`, гейт в `process_signal`, вызов в `run`), `backend/app/analytics/trading_config.py` (`RiskConfig`: env-переопределения `max_daily_loss_pct`, `max_position_size`, `max_open_positions`), `backend/alembic/versions/` (новая миграция `trading.live_equity`), `backend/app/api/live_trading_jobs.py` (эквайти-эндпоинт), `backend/tests/`.
- **Depends on:** A, B
- **Unblocks:** E, F
- **Relevant docs:** `project-context.ru.md` (риск-контур paper vs live), `handover.ru.md`.
- **Constraints:** без принудительного flatten; базис дневного убытка — по решению PO из Open Questions; `paper_equity` не затрагивается; сбой записи эквити не должен блокировать торговый цикл.
- **Acceptance:** после цикла в `trading.live_equity` появляется строка с `equity`, `peak_equity`, `drawdown_pct`; при `drawdown_pct >= max_daily_loss_pct` новые сигналы отклоняются с причиной `risk_breach` и алертом, существующие стопы сохраняются/довыставляются; позиция с нотилом больше `max_position_size` отклоняется; лимиты берутся из env с валидацией диапазона.
- **Regression:** paper-контур и его `write_equity` не изменяются.

### Задача E: Уведомления, heartbeat и мониторинг

- **Status:** planned
- **Goal:** все значимые события live-контура уходят в Telegram; отсутствие heartbeat обнаруживается; операционные метрики доступны через API.
- **Files:** `backend/app/analytics/live_executor.py` (инъекция `TelegramNotifier`, вызовы в open/close/trailing/protection-failure/risk-breach/kill-switch), `backend/app/notifications/telegram_notifier.py` (при необходимости — новые типы сообщений), `backend/app/api/live_trading_jobs.py` (метрики: состояние цикла, возраст heartbeat, счётчики ошибок, состояние защиты, риск-статус), `backend/tests/`.
- **Depends on:** B, C, D
- **Unblocks:** F
- **Relevant docs:** `project-context.ru.md` (уведомления), `handover.ru.md` (Telegram-токен в `.env`).
- **Constraints:** уведомления не должны ронять цикл (вызовы обёрнуты, для повторяющихся критических алертов — debounce); секреты не логируются; пороги алертов берутся из конфига, а не хардкодом.
- **Acceptance:** при открытии/закрытии/переносе стопа/сбое защиты/нарушении лимита/kill-switch уходит соответствующее сообщение (тесты на моке нотификатора); heartbeat обновляется каждый цикл, API отдаёт его возраст и признак stale; повторный критический алерт не спамит чаще заданного интервала.
- **Regression:** существующие paper-уведомления и контракты API панелей не ломаются.

### Задача F: Реальный брокер, глобальный kill switch, деплой и документация

- **Status:** planned
- **Goal:** контролируемое исполнение против реального счёта T-Bank возможно при явном включении, с глобальным аварийным остановом и задокументированным деплоем.
- **Files:** `backend/app/broker/tinkoff_live.py` (новый), `backend/app/broker/tinkoff_sandbox.py` (общий контракт), `backend/app/analytics/trading_config.py` (`allow_real_trading`, выбор клиента), `backend/app/analytics/live_executor.py` (проверка `live_trading_kill_switch` в `app_settings`, инициализация клиента), `backend/app/api/live_trading_jobs.py` (kill-switch endpoint), `backend/alembic/versions/` (ключ `live_trading_kill_switch`), `docker-compose.yml`, `backend/start_processes.sh`, `.env.example`, `docs/agents/project-context.{ru,}.md`, `docs/agents/handover.{ru,}.md`.
- **Depends on:** C, D, E
- **Unblocks:** —
- **Relevant docs:** `handover.ru.md` (alembic, запуск), `project-context.ru.md` (архитектура брокера).
- **Constraints:** по умолчанию — только песочница; реальный режим включается исключительно явной парой условий (`allow_real_trading=True` и kill switch не активен) и логируется при старте; `alembic upgrade head` — обязательный шаг деплоя, авто-миграций на старте приложения не добавляем; токены реального контура — отдельные env-переменные, не смешиваются с sandbox-токеном.
- **Acceptance:** `TinkoffLiveClient` реализует тот же контракт, что sandbox (включая stop-методы), и покрыт тестами на моках gRPC; при `live_trading_kill_switch=true` исполнитель не открывает позиции и пишет алерт; деплой прогоняет миграции до старта backend; `.env.example` содержит все новые переменные; runbook деплоя и отката описан в docs (RU+EN).
- **Regression:** песочничный контур и существующие тесты работают без изменения поведения.

## Таблица соответствия Issues

| Задача | GitHub Issue | Ветка |
| --- | --- | --- |
| Epic | #172 | `feature/epic-001-live-production-readiness` |
| A — Schema и preflight | #173 | `feature/issue-173-live-schema-preflight` |
| B — Живучесть цикла, shutdown, lock | #174 | `feature/issue-174-executor-resilience` |
| C — Брокерская защита и реконсиляция | #175 | `feature/issue-175-broker-stop-protection` |
| D — Live equity и риск-гейты | #176 | `feature/issue-176-live-equity-risk-gates` |
| E — Уведомления и мониторинг | #177 | `feature/issue-177-live-notifications-monitoring` |
| F — Реальный брокер, kill switch, деплой | #178 | `feature/issue-178-live-broker-kill-switch-deploy` |

## Risks

- **Постановка стоп-ордера отклоняется брокером (неверный шаг цены/лота, минимум, неликвид).** Вероятность: высокая. Митигация: округление по `min_price_increment` и `lot`, повтор с backoff, `notify_critical`, позиция помечается `protection_failed` и берётся под усиленный мониторинг; тесты на реальных шагах цены из `get_instrument_by`.
- **Amend трейлинга неатомарен: между cancel и post позиция остаётся без стопа.** Вероятность: высокая. Митигация: сначала post нового стопа, затем cancel старого (либо немедленный повтор при сбое), проверка инварианта каждый цикл, алерт при `broker_stop_id IS NULL`.
- **Исчерпание лимита API (0, 10] при amend'ах на нескольких позициях.** Вероятность: средняя. Митигация: приоритизация (защита > трейлинг > входы), перенос задач на следующую итерацию, счётчики в метриках.
- **Риск-гейт блокирует торговлю из-за неверного базиса дневного убытка.** Вероятность: средняя. Митигация: явное решение PO (Open Questions), dry-run логирование гейта до включения в бой.
- **Реальный счёт исполняет рыночный стоп с большим проскальзыванием.** Вероятность: высокая в волатильные дни. Митигация: учёт `slippage_bp`/`slippage_r` из филлов, алерт при превышении порога, возможность переключить тип стопа конфигом.
- **Дрейф схемы между средой разработки и продом.** Вероятность: средняя. Митигация: fail-fast preflight (задача A), `alembic upgrade head` в деплое, проверка колонок и статусов при старте.
- **Открытая позиция в боевой БД без стоп-ордера на момент переключения.** Вероятность: средняя (на 2026-09-18 есть 1 `open`). Митигация: явный ручной перенос по runbook; до выставления стопа новые входы запрещены.
- **Дубликат исполнителя из-за неработающего advisory lock.** Вероятность: средняя. Митигация: выделенное соединение для lock/unlock (задача B), тест, метрика `lock_holder_pid`.

## Open Questions

- Точные значения `max_daily_loss_pct`, `max_position_size`, `max_open_positions` для прода → PO → до начала задачи D.
- Базис дневного убытка: календарный день по МСК, торговая сессия или скользящие 24 ч; точка сброса `peak_equity` → PO → до начала задачи D.
- Политика дивидендных/новостных гэпов: переносить стоп через гэп или закрывать позицию → PO → до начала задачи C.
- Допустимо ли ручное вмешательство в позицию (частичное закрытие) при работающем исполнителе и как оно согласуется с amend'ом стопа → PO → до начала задачи C.
- Пороги алертов: проскальзывание (bp), возраст heartbeat, число подряд идущих ошибок цикла → PO → до начала задачи E.
- Порядок восстановления после breach: ручной сброс флага, автосброс на следующую сессию или по восстановлению equity → PO → до начала задачи D.
- Нужен ли отдельный `account_id` для реального счёта и один ли счёт на все стратегии → PO → до начала задачи F.
- Требуется ли OCO-связка стопа и TP на стороне брокера вместо локальной реконсиляции → PO → как возможный follow-up после задачи C.

## Progress Log

- 2026-09-18 Epic created. План сформирован по итогам анализа кода, 68 зелёных тестов `backend/tests/test_live_executor.py` и read-only проверки боевой БД (`trading.live_positions`: 30 колонок, CHECK из 7 статусов; `live_equity` отсутствует; `app_settings.value` — JSONB; `alembic_version` присутствует).
- 2026-09-18 Созданы epic-Issue #172 и задачи #173-#178 (A-F), метка `epic-172`. Статус эпика — in-progress, старт реализации с задачи A (#173).
