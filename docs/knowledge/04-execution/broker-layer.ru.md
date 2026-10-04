# Брокерский слой: песочница и реальный контур (задача #178, эпик #172, блок F)

> **Source:** project-context.ru.md sections 24
> **Last refreshed:** 2026-10-04, task-346

## 24. Брокерский слой: песочница и реальный контур (задача #178, эпик #172, блок F)

До #178 исполнение существовало только против песочницы. Теперь брокерский слой —
три модуля и одна точка выбора:

```
backend/app/broker/
├── tinkoff_sandbox.py  TinkoffSandboxClient -> client.sandbox.*  (INVEST_GRPC_API_SANDBOX)
├── tinkoff_live.py     TinkoffLiveClient    -> orders / stop_orders / operations / users
└── client_factory.py   create_execution_client() — единственный выбор контура
```

`LiveExecutor` не знает, каким клиентом торгует: контракт duck-typed и совпадает
метод в метод (`execute_order`, `cancel_order`, `get_orders`, `post_stop_order`,
`get_stop_orders`, `cancel_stop_order`, `get_operations`, `get_positions`,
`check_balance`), а возвращаемые структуры — те же dataclass'ы (`tinkoff_live`
переэкспортирует их как `LiveOrder = SandboxOrder` и т.д.). Ошибки реального
контура наследуют sandbox-ошибки (`LiveAPIError(SandboxAPIError)`,
`LiveConfigurationError(SandboxConfigurationError)`), поэтому ни один
`except SandboxAPIError` в исполнителе менять не пришлось. Retry-политика,
конвертация `Quotation` и маппинги строк в SDK-enum'ы — общие (импортируются из
sandbox-модуля, второй копии нет).

### 24.1 Маппинг сервисов

| Операция | песочница | реальный контур |
|---|---|---|
| Заявка | `sandbox.post_sandbox_order` | `orders.post_order` (idempotency — `idempotence_id`) |
| Отмена заявки | `sandbox.cancel_sandbox_order` | `orders.cancel_order` |
| Активные заявки | `sandbox.get_sandbox_orders` | `orders.get_orders` |
| Стоп-заявка | `sandbox.post_sandbox_stop_order` | `stop_orders.post_stop_order` |
| Список стопов | `sandbox.get_sandbox_stop_orders` | `stop_orders.get_stop_orders` (без фильтра по датам) |
| Отмена стопа | `sandbox.cancel_sandbox_stop_order` | `stop_orders.cancel_stop_order` |
| Операции/филлы | `sandbox.get_sandbox_operations` | `operations.get_operations` |
| Портфель | `sandbox.get_sandbox_portfolio` | `operations.get_portfolio` |
| Деньги | `sandbox.get_sandbox_positions` | `operations.get_positions` |
| Счета | `sandbox.get_sandbox_accounts` | `users.get_accounts` |

Все методы реальных сервисов в `t-tech-investments` 1.51.0 — keyword-only и не
принимают объект `request=` (его принимает только `SandboxService`); ключ
идемпотентности `orders.post_order` — `order_id`. Issue #192 нашёл четыре места,
где это нарушалось: клиент аутентифицировался и читал реальный счёт, но любой вызов
заявки и стоп-заявки падал с `TypeError` ещё до отправки запроса — незаметно для
фейков, принимавших любые аргументы. Поэтому `backend/requirements.txt` пинит
`t-tech-investments==1.51.0` (и `sqlalchemy<2.1`, см. §46.7 handover), а
`tests/test_tinkoff_live.py` биндит каждый брокерский вызов на сигнатуру
установленного SDK через `sdk_bound_stub()`.

### 24.2 Точка выбора контура

Единственный источник истины — `SANDBOX_TRADING.allow_real_trading`
(`trading_config.py`), в коде `False`. Env-override `ALLOW_REAL_TRADING`
резолвится в `get_sandbox_trading_config()` через `_env_strict_bool()`:
неоднозначное значение → `ValueError` на старте. Фабрика
`create_execution_client()` возвращает `TinkoffLiveClient` (лог WARNING) или
`TinkoffSandboxClient` (лог INFO); выбранный контур публикуется как
`broker_contour` в снимке метрик и в заголовках алертов. При открытом gate
sandbox-клиент отказывается конструироваться — смешать контуры в одном процессе
нельзя. Учётные данные разделены: `TINVEST_TOKEN`/`TINVEST_ACC` (market data),
`TINVEST_SANDBOX`/`TINVEST_SANDBOX_ACC` (песочница),
`TINVEST_LIVE_TOKEN`/`TINVEST_LIVE_ACC` (реал); cross-fallback запрещён кодом.
С Issue #192 один физический токен может обслуживать market data и реальный контур,
но только через явный opt-in `ALLOW_LIVE_TOKEN_REUSE=true` (в коде `False`, строгий
парсинг, пишется WARNING); отказ при совпадающих значениях остаётся поведением по
умолчанию.

### 24.3 Глобальный kill switch

`trading.app_settings.live_kill_switch` (миграция `20260928_001`, runtime-сид в
`live_schema.LIVE_SCHEMA_STATEMENTS`, ключ входит в
`REQUIRED_APP_SETTINGS_KEYS`). Гейт стоит в `process_signal` **первой** бизнес-
проверкой — до сессионного окна, стакана, сайзинга и любого брокерского вызова;
отказ логируется причиной `kill_switch` и считается в
`kill_switch_rejections_total`. Fail-safe (решение D2): отсутствие строки, `NULL`
или ошибка чтения → ВКЛЮЧЕНО; in-memory дефолт тоже `true`, а `initialize()`
читает сохранённое значение тихо, до первого цикла. Открытые позиции не
затрагиваются: стопы не снимаются, flatten отсутствует. Управление —
`POST /api/live-trading/kill-switch` (upsert + подтверждение чтением, 503 при
недоступной таблице) или SQL напрямую; состояние публикуется секцией
`global_kill_switch` в `GET /api/live-trading/metrics`, а `state` становится
`kill_switch` при любом из двух рычагов.

### 24.4 Деплой и миграции

Миграции — явный шаг деплоя (решение D4): one-shot сервис `migrate`
(`alembic upgrade head`) в `docker-compose.yml`, `backend` ждёт его через
`depends_on: {migrate: {condition: service_completed_successfully}}`. Образ
содержит `alembic.ini` и `alembic/`. Оба сервиса используют один env-блок
(YAML-anchor `x-backend-env`), а `get_app_database_url()` читает пароль как
`POSTGRES_PASSWORD` → `PSTGRS_PWD` → `app`, поэтому DSN миграций и приложения
совпадают. Автомиграций в коде приложения нет; `.env.example` выведен из-под
`.gitignore` исключением `!.env.example` (закрыт D14 из #176).

**Тесты:** `test_tinkoff_live.py` (51), `test_live_kill_switch.py` (41),
`test_deploy_migrations.py` (15). Операционные детали и runbook перехода на
реальный счёт — `handover.ru.md` §46.

### 24.5 Инструменты верификации (задача #192)

У брокерского слоя есть две read-only диагностики, они лежат вместе с рабочими
артефактами в
`reports/190-production-trading-infrastructure/192-g1-production-client-verify/`:

- `192-contract-check.py` — проверяет контракт безопасности без учётных данных и
  без сетевых вызовов: глобальный гейт закрыт, фабрика по умолчанию отдаёт
  песочничный клиент, принудительная сборка реального клиента падает fail-closed,
  паритет методов и именованных аргументов live/sandbox соблюдён, мутирующие и
  read-only методы классифицированы верно, типы ошибок live наследуют
  песочничные.
- `192-live-smoke.py` — read-only smoke реального контура с сухим прогоном
  `--self-test` на внутрипроцессном fake-клиенте. Вместо переключения
  `ALLOW_REAL_TRADING` используется диагностический аргумент конструктора
  `allow_real_trading=True`, а мутирующие методы затеняются заглушками до первого
  вызова API; читаются только `get_accounts` / баланс / позиции / заявки /
  стоп-заявки / операции. Токены и идентификаторы счетов маскируются в каждом
  артефакте.

Порядок запуска, правила маскирования и текущий заблокированный статус —
`handover.ru.md` §46.10.

### 24.6 Остановка потока без flatten-all: три рычага (задача #193)

Эпик #190 не допускает flatten-all в продукт: остановка потока ничего не
продаёт. Останавливают поток три независимых рычага, все поставлены раньше
(#151, #174, #178), и различаются они тем, чего касаются:

| Рычаг | Источник истины | Новые входы | Трейлинг-храповик | Открытые позиции | Брокерские стопы |
|---|---|---|---|---|---|
| Глобальный kill switch | `trading.app_settings.live_kill_switch`; `POST /api/live-trading/kill-switch` | блокируются, причина `kill_switch`, счётчик `kill_switch_rejections_total` | продолжает работать | не трогаются | не трогаются |
| Трейлинг kill switch | `trading.app_settings.trailing_kill_switch`; только SQL, API-эндпоинта нет | разрешены | заморожен: нет арминга, переноса и брокерского amend | не трогаются | не трогаются |
| SIGTERM / SIGINT | `install_signal_handlers()` → `shutdown_requested` → `shutdown()` | процесса нет, входов нет | останавливается вместе с процессом | не трогаются, flatten отсутствует (`close_positions_on_shutdown=false`) | остаются выставленными; отменяются только pending-заявки на вход, помечаясь `cancelled` |

Оба переключателя перечитываются каждый цикл в `_refresh_kill_switch()`
(≤ `check_interval_seconds`, 30 с), поэтому рестарт не нужен, а каждый переход —
одноразовое событие в Telegram со собственным происхождением значения. Правила
fail-safe намеренно разные: нечитаемый `live_kill_switch` (нет строки, `NULL`,
ошибка БД) означает ВКЛЮЧЕНО, тогда как отсутствующая строка
`trailing_kill_switch` сохраняет исторический fail-open `False`, и только ошибка
БД взводит её.

`shutdown()` — единственное место, которое что-либо отменяет, и отменяет оно
только pending-входы: для строки `open` при `close_positions_on_shutdown=false`
он пишет в лог `Position <id> left protected with broker_stop_id=...`, ничего не
пишет в `live_positions`, освобождает advisory lock `151001` и принудительно
сохраняет финальный снимок метрик (решение D5). Рестарт восстанавливает всё из
БД. Позиция, закрытая руками в приложении брокера, на следующем цикле
реконсилируется через `GetOperations` и `_classify_exit_reason` и попадает в
`closed_take` / `closed_broker`: причины «manual» в замороженном списке статусов
#173 нет, поэтому доверять надо `exit_price_actual` / `lots_executed`.

Проверка: `193-shutdown-drill.py` в
`reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten/`
— по построению песочничный, все мутирующие методы брокера под заглушками, строка
`live_kill_switch` восстанавливается после round-trip, `DRILL_OK` от 2026-09-30 при
`mutating calls: 0` — плюс регрессии #174/#151/#178. Матрица для оператора,
runbook A/B/C и порядок запуска drill — `handover.ru.md` §46.11.



### 24.7 Сквозная зачистка сиротских стоп-ордеров (задача #199)

`LiveExecutor` закрывает цикл, открытый drill #193. `_sweep_orphan_stops()` — последний проход `_finish_monitor_cycle()`, поэтому он выполняется и на раннем возврате при пустой книге позиций (именно тогда любой оставшийся у брокера стоп уже ничей) и снимает ACTIVE SELL-стопы, которые больше не защищают ни одной живой позиции. Допуск — единая fail-closed цепочка (`_orphan_stop_skip_reason`): стоп должен быть ACTIVE, типа SELL/STOP_LOSS, входить в настроенный live-универсум (тикер / FIGI / instrument_uid), не упоминаться ни одной строкой `pending`/`open` в `live_positions`, не быть заявленным ни OCO-проходом #175, ни любой из сторон ожидающего amend, не опираться на брокерскую позицию по тому же инструменту и быть старше `orphan_stop_grace_seconds`. Всё остальное сохраняется и логируется с причиной.

Право действовать требует доказательств с двух сторон во времени: один и тот же сирота должен быть увиден `orphan_stop_confirmations` проходами подряд (таблица кандидатов каждый проход заменяется целиком, поэтому «мерцающий» стоп подтверждений не накапливает), а проход, нашедший больше подтверждённых сирот, чем `orphan_stop_max_cancels`, не снимает ничего и взводит `orphan_sweep_fail_closed_total` плюс прореженный critical-алерт: полная сирот книга стопов означает, что модель счёта неверна, а отмена — способ превратить эту ошибку в реальные деньги. Ручки живут в `LIVE_TRADING` (`orphan_stop_*`, перечитываются на каждом проходе, валидируются на старте), счётчики публикуются в `protection.orphan_*` на `/api/live-trading/metrics`, а `shutdown()` теперь передаёт `force=True` в `_cancel_pending_stops()`, чтобы вытесненный amend-трейлингом, но не подтверждённый стоп всё равно снимался на выходе вместо того, чтобы стать следующим сиротой. Операционные детали: `handover.ru.md` §46.12.


### 24.8 Canary-контур: один тикер, один лот, две паузы оператора (задача #194)

`CANARY` в `trading_config.py` — вся политика целиком (`enabled=False`, `ticker='SBER'`, `max_lots=1`, `max_open_positions=1`, `allow_outside_entry_window=False`), плюс `CANARY_BOUNDS` для двух целочисленных диапазонов и `CANARY_ENV` для четырёх env-ручек (`CANARY_ENABLED`, `CANARY_TICKER`, `CANARY_MAX_LOTS`, `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW`). Красная линия проекта запрещает хардкодить тикер где-либо ещё, поэтому расширение canary — это ревьюимое изменение конфига, а расширение *за его пределами* требует сначала go/no-go CEO по отчёту canary. `get_canary_config()` сливает дефолты → env → словарь вызывающей стороны и валидирует через `normalize_canary_ticker()` / `validate_canary_values()`: пустая ручка означает «не задано», испорченная бросает `ValueError` на старте, так что контур никогда не стартует на догадке о том, каким именем и каким потолком он торгует.

`LiveExecutor` разрешает эту политику *до* `_validate_config()` (canary сужает `max_open_positions` до `min(config, canary)`, никогда наоборот) и далее работает как боевой контур с четырьмя дополнительными ремнями. `_apply_canary_universe()` сужает live-универсум до единственного canary-тикера и fail-closed уходит в ПУСТОЙ универсум, если `trading_universe` не помечает его как live-enabled; `process_signal()` повторяет этот гейт сразу после kill switch и до сессионного окна, стакана, сайзинга и любого брокерского вызова (причина `canary_universe`), поэтому прямой вызов не может протащить чужое имя; размер входа ограничивается после сайзера и после гейта нотила #176 с собственным кодом причины `canary_cap` (потолок только уменьшает и никогда не трогает лимиты `LIVE_RISK` — решение D3); две блокирующие паузы читают ответ через инжектируемую `confirm_fn` (по умолчанию `_stdin_confirm`, где EOF означает «нет») — `_canary_confirm_entry()` до `execute_order` и `_canary_post_entry_gate()` после филла и его защиты, где `retry` перевыставляет только недостающую ногу (`_canary_rearm_protection()` никогда не дублирует вооружённый стоп), а отказ вызывает `_canary_abort()`. Один ремень работает в обратную сторону и включается явно (решение PO от 2026-10-03): `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` разрешает **только этому canary** входить вне календаря #137 ([10:00, 19:00) MSK по будням) — это и нужно прогону в выходной / внебиржевую сессию. Единственный читатель — `_entry_window_open()`: гейт входа в `process_signal()`, гейт `process_latest_bars()` главного цикла и `wait_for_session_open()`, который в этом случае возвращается сразу, а не спит до понедельника; в поставке флаг `false`, обычный прогон ручку не читает, а все остальные гейты (kill switch, canary-универсум, риск, свежесть стакана, дисбаланс, сайзинг, потолок в 1 лот, обе паузы) продолжают работать.

Abort — это асимметрия #193 в canary-форме: `request_shutdown()` плюс `_canary_abort_no_flatten`, который внутри `shutdown()` принудительно выключает `close_positions_on_shutdown`, чтобы исполненная позиция сохранила брокерские стоп/тейк и дождалась ручного runbook вместо ликвидации из-за отклонённого промпта. Счётчики (`canary_capped_total`, `canary_rejections_total`, `canary_confirmations_total`, `canary_confirm_retries_total`, `canary_aborts_total`, `canary_window_bypass_total`) и поля идентичности (`canary_enabled`, `canary_ticker`, `canary_max_lots`, `canary_allow_outside_entry_window` — `None`, а не `0`, когда canary выключен) попадают в `get_metrics()`, в персистентный снимок §23 и в собственную секцию `canary` на `GET /api/live-trading/metrics`; `_canary_lines()` дописывает canary-блок в алерты `live_start` / `live_entry` только внутри canary. Песочный drill (`194-canary-drill.py`, десять фаз, мутирующие методы брокера затенены счётными заглушками) и операторский runbook — в `handover.ru.md` §46.14.


## Приложение (2026-09-27, follow-up #177): тайминг алерта live_start
Алерт `live_start` отправляется сразу при старте процесса `LiveExecutor` - до ночного ожидания сессии 10:00 MSK (`wait_for_session_open()`), поэтому воскресный запуск виден в Telegram немедленно. Поскольку алерт идёт до `initialize()`, поле «Тикеров» равно 0 и имя стратегии пусто до открытия сессии - это ожидаемое значение «ещё не инициализировано», а не дефект. `check_interval` вычисляется до формирования алерта, поэтому payload не может упасть на неинициализированной переменной.
