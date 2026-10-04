# Sandbox live executor

> **Source:** project-context.ru.md sections 13 + handover.ru.md sections 15, 33, 43
> **Last refreshed:** 2026-10-04, task-346

## 13. Sandbox live executor

`backend/app/analytics/live_executor.py` реализует `LiveExecutor` без изменений `StrategyEvaluator`. При инициализации он пересекает тикеры locked paper-стратегии с `get_live_trading_universe()` (PO-список: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL), чтобы sandbox-ордера оставались на настроенных live-именах. Для каждого тикера он загружает активную заблокированную стратегию и 4h-контекст, передаёт последнюю закрытую строку из `online_candles_1min` в `check_entry` и до любого обращения к брокеру применяет обязательный фильтр свежего imbalance. Прошедший BUY проверяет свободные RUB, рассчитывает размер через `calculate_position_size`, отправляет sandbox market-ордер и сохраняет broker IDs и состояние в `trading.live_positions`. Каждый отказ BUY пишется одной структурированной строкой с тикером, стабильным кодом `reason` и релевантными числами; тишина в `executor.log` означает, что `StrategyEvaluator` не дал BUY, а не то, что фильтр мёртв. Read-only preflight — в `live_executor_preflight.py`; overnight runbook — handover §15 / §33 (историческая canary: §19). Задача #137: `until_session_end=True` ждёт 10:00 МСК (часы компьютера → UTC+3) до `initialize()`, отклоняет новые входы вне [10:00, 19:00) с `reason=outside_entry_window` и держит стоп/тейк до закрытия позиции по цене (в том числе после 19:00). Границы сессии — в `MOEX_SESSION` / `moex_session.py`.

**Задача #151 (завершена 2026-09-16)**: Live trailing-stop с храповиком, рестартом и kill-switch. При открытии позиции, если `trailing_enabled=true` в конфиге стратегии и kill switch выключен, executor армит трейлинг (0 брокерских вызовов). `monitor_positions()` вызывает `_apply_trailing()` для продвижения лестницы бар за баром через условный UPDATE (`WHERE step_reached < new_step` для монотонности). При выходе записывает `exit_price_model` vs `exit_price_actual` со слиппеджем в б.п. и R. Kill switch (`trading.app_settings.trailing_kill_switch`) приостанавливает арминг/храповик без остановки executor. Advisory lock `pg_try_advisory_lock(151001)` предотвращает запуск нескольких экземпляров. Полный контракт — в §21.

**Задача #173 (2026-09-18)**: контракт схемы live_positions с fail-fast проверкой. Модуль `backend/app/analytics/live_schema.py` — единственный источник формы `trading.live_positions`, требуемой цепочкой миграций (`20260915_002` + `20260916_001`): 30 колонок, CHECK из семи статусов (`pending`, `open`, `closed_stop`, `closed_take`, `closed_trailing`, `closed_broker`, `cancelled`) и `trading.app_settings` с сидами `trailing_kill_switch` / `live_trailing_enabled`. `ensure_live_positions_table()` делегирует в `ensure_live_positions_schema()`, чей идемпотентный DDL — суперсет обеих миграций; он же приводит к целевой форме базы, созданные старым 20-колоночным runtime-DDL. `LiveExecutor.initialize()` сразу после DDL вызывает `assert_live_schema()` и прерывает запуск с `LiveSchemaError` (перечень недостающих колонок/статусов и подсказка `alembic upgrade head`), поэтому дрейф ловится до первого ордера, а не в бою (`UndefinedColumn` на трейлинг-полях либо нарушение CHECK на `closed_trailing`). `live_executor_preflight.collect_preflight()` отдаёт тот же контракт как блокирующую проверку `live_positions_schema` и блок `details.live_schema` (`columns_found`, `missing_columns`, `statuses_found`, `missing_statuses`). Отсутствие ключей `app_settings` и лишние колонки — предупреждения, а не блокировка.

Take-profit сразу выставляется как ожидающий sell-limit. Начиная с задачи #175 stop-loss — это реальный брокерский стоп-ордер `STOP_LOSS`, который ставится сразу после исполнения входа (`broker_stop_id`); исторический синтетический путь (дождаться, пока текущая цена брокера достигнет stop, отменить take и выставить sell-limit по наблюдаемой цене) остался только как fallback, пока у позиции нет брокерского стопа. Сверка позиций опрашивает `get_positions()`; исчезновение позиции после take или сработавшего stop закрывает строку БД, классифицируется по авторитетному статусу стоп-ордера и сверяется с реальным филлом из `get_operations()`. Внешний SELL отменяет защитные заявки (стопы — через `cancel_stop_order()`) и закрывает позицию sandbox market-ордером.

**Задача #174 (2026-09-19)**: живучесть цикла и безопасный останов. Главный цикл `run()` защищён от транзиентных ошибок: вызовы `monitor_positions()` и `process_latest_bars()` обёрнуты в `try/except` со счётчиком последовательных ошибок (`MAX_CONSECUTIVE_ERRORS = 5`); при превышении порога пишется критический алерт и исполнитель корректно останавливается. Ошибки изолированы на уровне единицы работы — пер-тикер в `process_latest_bars()` и `refresh_contexts()`, пер-позиция в `monitor_positions()` — поэтому сбой по одному тикеру или позиции не прерывает обработку остальных. Безопасный останов: при `close_positions_on_shutdown=false` (дефолт) открытые позиции **не** остаются без защиты — стоп/тейк-ордера остаются на стороне брокера, отменяются только ожидающие входные ордера (`pending`). Блокировка `pg_try_advisory_lock(151001)` теперь выполняется на **выделенном соединении**, которое удерживается на протяжении всей жизни процесса и освобождается на том же соединении в `shutdown()`, что исключает утечку блокировки между соединениями пула. Пульс и метрики: `heartbeat_ts`, `iterations_total`, `errors_total`, `errors_consecutive`, `last_error_at` обновляются на каждой итерации; метод `get_metrics()` отдаёт словарь для внешнего мониторинга (задача E #177). Тесты: `test_live_executor.py` (78 тестов, включая 10 новых для #174).

**Задача #200 (2026-09-30)**: счётчик последовательных ошибок из #174 считает **неудачные циклы**, а не неудачные фазы. Раньше каждая фаза сбрасывала общий счётчик собственным успехом, поэтому внутри окна входа MOEX постоянно падающий `monitor_positions()` — защита стоп/тейк открытых позиций — маскировался до тех пор, пока `process_latest_bars()` продолжал завершаться успешно: серия обнулялась каждый цикл, порог `MAX_CONSECUTIVE_ERRORS` не достигался, и контур продолжал торговать с незащищёнными позициями. Симметрично: две падающие фазы добавляли 2 за цикл, поэтому документированные «5 подряд» фактически срабатывали на третьем. Теперь обе фазы выполняются, их сбои сводятся в одно решение на цикл, `_consecutive_errors` растёт ровно на 1 за неудачный цикл и сбрасывается только после полностью чистого; `errors_total` по-прежнему считает каждый отдельный сбой фазы, а критический алерт `consecutive_errors` называет фазу, упавшую первой, — потеря защиты важнее потерянного входа. Та же задача добавляет `stopped_reason` в `get_metrics()` и в секцию `loop` ответа `/api/live-trading/metrics`: `max_consecutive_errors`, `session_end`, `duration`, `signal` или `exception`, и `null`, пока контур ещё работает. У остановившегося процесса по определению нет heartbeat, поэтому `state` способен сообщить лишь `stale`; именно `stopped_reason` отличает «исполнитель сам остановился, защитив счёт» от «исполнитель умер». Тесты: два теста цикла `run()` в `test_live_executor.py` больше не зависят от реальных часов и процессных обработчиков SIGTERM/SIGINT — раньше их исход определялся временем суток, в которое запускался прогон, поэтому дефект и оставался невидимым вне торговых часов.

**Задача #175 (2026-09-22)**: брокерская защита позиций, amend-трейлинг и сверка по филлам. В `TinkoffSandboxClient` добавлены `post_stop_order()` (`PostStopOrder`: STOP_LOSS/TAKE_PROFIT, buy/sell, защитная лимитная цена, клиентский ключ идемпотентности, `good_till_cancel`/`good_till_date`), `get_stop_orders()` (`GetStopOrders` с фильтром статуса и клиентской фильтрацией по инструменту — в API её нет), `cancel_stop_order()` (`CancelStopOrder`), `get_orders()` (`GetOrders`, ожидающие заявки) и `get_operations()` (`GetOperations`, исполненные операции с филлами); все они переиспользуют прежнюю политику retry/backoff и sandbox-предохранители. `LiveExecutor` ставит `STOP_LOSS` сразу после исполнения входа — триггер округляется вниз до `min_price_increment`, лимит на `trailing_protective_ticks` тиков ниже триггера — и сохраняет id в `broker_stop_id`. Каждый шаг трейлинга выполняется как amend через дублирование (постановка более высокого стопа → подтверждение через `GetStopOrders` → отмена старого, более низкого), поэтому защита не прерывается; если подтверждение неоднозначно, старый стоп снимает `_cancel_pending_stops()` (при подтверждении либо не позднее `oco_check_delay_seconds`), что ограничивает окно двойной защиты. Выходы сверяются с реальными филлами: `get_operations()` даёт `exit_price_actual` и `lots_executed` (акции → лоты по `lot_size`, цена взвешивается по филлам), причина определяется по авторитетному статусу стопа (`EXECUTED` → `closed_stop`/`closed_trailing`, ещё `ACTIVE` → `closed_take`), затем по цене филла относительно модельных уровней и в последнюю очередь по прежнему эвристическому правилу id (при неоднозначности — `closed_broker`); рядом пишутся `slippage_bp` / `slippage_r`. OCO эмулируется мониторингом: после каждого закрытия обе ноги ставятся в очередь, и через `oco_check_delay_seconds` уцелевший стоп/тейк отменяется с критическим алертом `OCO monitoring:` (не более `oco_check_attempts` попыток). Два инварианта проверяются каждые `broker_stop_verify_interval_seconds` — нет открытой позиции без `broker_stop_id` и нет сохранённого `broker_stop_id`, отсутствующего в активном списке брокера; нарушение пишет критический `Invariant violation:` и повторяет постановку с экспоненциальным backoff, сбои постановки логируются как `protection_failed` и видны в `get_metrics()` (`stops_armed_total`, `stop_amend_total`, `stop_amend_failed_total`, `protection_failed_positions`, `invariant_violations_total`, `oco_orphans_cancelled_total`, `fills_reconciled_total`). Token bucket приоритизирован: `protection` > `trailing` > `entry` (входы оставляют `entry_token_reserve` токенов). Новые ключи `LIVE_TRADING`: `broker_stop_enabled`, `protection_retry_seconds`, `broker_stop_verify_interval_seconds`, `oco_check_delay_seconds`, `oco_check_attempts`, `operations_lookback_hours`, `entry_token_reserve`; схема `live_positions` из #173 не менялась. Тесты: `test_live_executor.py` (94 теста, включая 16 новых для #175) и `test_tinkoff_sandbox.py` (28 тестов, включая 15 новых для #175).

Каждая попытка обращения к broker API, включая внутренние retry и обнаружение счёта, проходит через token bucket с потолком 10 запросов/сек и классами приоритета задачи #175: `protection` (постановка стопа, amend, отмена, сверка филлов) и `trailing` (мониторинг) проходят всегда, а вызовы `entry` обязаны оставлять `entry_token_reserve` токенов, поэтому насыщенный bucket откладывает новые входы, а не защиту. SIGTERM/SIGINT только устанавливает флаг остановки; финальная очистка отменяет ожидающие входные заявки и брокерские стопы, обновляет состояние БД, при включённом `close_positions_on_shutdown` закрывает открытые sandbox-позиции, освобождает advisory lock и закрывает DB pool standalone-процесса. Полную политику возвращает `get_live_trading_config()` из `trading_config.py`.

## 15. Эксплуатация sandbox live executor

- Условия запуска: backend пересобран, streaming online data работает, активна одна заблокированная стратегия, sandbox-счёт пополнен и `LIVE_TRADING.enabled=true`.
- При подготовке новой БД примените миграцию явно: `psql ... -f backend/migrations/20260817_01_live_positions.sql`. `LiveExecutor.initialize()` также автоматически применяет ту же идемпотентную схему.
- Безопасный overnight-запуск (задача #137): пересобрать backend, затем `START_LIVE_EXECUTOR=1 ./start_processes.sh` **без** `DURATION_MINUTES`. Paper-процессы живут до ближайшего будничного **10:00 после 19:00 этой сессии** (стрим для оставшихся стоп/тейк). LiveExecutor спит до 10:00 МСК, **входит только 10:00–19:00**, затем держит стоп/тейк до закрытия позиции по цене. Часы — системные, приведённые к МСК (UTC+3). `START_LIVE_EXECUTOR=1` остаётся opt-in, чтобы обычный paper-запуск не выставлял sandbox-ордера. Лог: `reports/live-executor/executor.log`.
- Документация и опубликованные артефакты расходятся: если `report.md` и документ агента спорят о числе сеток, схеме решётки или ключевой цифре — верьте артефактам и перерендерьте: `python analytics/issue-143-trailing-robustness/run.py --stage report`, затем `cd backend && python -m pytest -q tests/test_issue155_analysis.py`. Числа решётки не переносят из текста PR или коммита.
- Canary / фиксированное окно: `DURATION_MINUTES=N` по-прежнему стартует сразу и останавливается через N минут после запуска. Так нельзя запускать overnight вс.→пн.
- Входы LiveExecutor только в [10:00, 19:00) МСК (`reason=outside_entry_window`), даже если `StrategyEvaluator.entry_window` равен 7–19. Стоп/тейк срабатывают по цене и после 19:00; процесс сам останавливается только когда позиций не осталось (или по SIGTERM). Политика shutdown без изменений (`close_positions_on_shutdown=false`).
- Порядок обработки фиксирован: BUY от `StrategyEvaluator` -> окно сессии -> свежий imbalance -> свободные RUB -> position sizing -> market BUY -> брокерский `STOP_LOSS` -> take sell-limit -> запись/сверка БД.
- Stop-защита — реальный брокерский стоп-ордер (задача #175). Сразу после исполнения входа `post_stop_order()` выставляет `STOP_LOSS`: триггер = модельный стоп, округлённый вниз до `min_price_increment`, лимит = триггер минус `trailing_protective_ticks` тиков; id сохраняется в `broker_stop_id`. Нельзя выставлять обычный sell-limit при входе: limit ниже рынка исполнился бы сразу. Синтетический стоп до #175 (отмена take и sell-limit по наблюдаемой цене) остался только как fallback, пока `broker_stop_id` пуст.
- Трейлинг выполняется как amend через дублирование (решение PO от 2026-09-22): `PostStopOrder` (новый, более высокий стоп) -> `GetStopOrders` (подтверждение активности) -> `CancelStopOrder` (старый, более низкий), поэтому позиция никогда не остаётся без защиты. Если подтверждение неоднозначно, старый стоп снимает `_cancel_pending_stops()` — при следующем подтверждении либо не позднее `oco_check_delay_seconds`, так что два активных стопа не переживают grace-период.
- Каждая физическая попытка broker API, включая retry SDK и обнаружение счёта, использует один token bucket (`api_rate_limit`, максимум 10/сек) с классами приоритета (задача #175): `protection` > `trailing` > `entry`; входные вызовы обязаны оставлять `entry_token_reserve` токенов для защиты. Не добавляйте вызовы в обход `_broker_call` или клиентского hook `before_request`.
- Выходы сверяются с реальными филлами: когда позиция исчезла из портфеля брокера, `get_operations()` даёт `exit_price_actual` и `lots_executed` (акции переводятся в лоты по `lot_size`), причина выхода определяется по авторитетному статусу стопа (`EXECUTED` -> `closed_stop`/`closed_trailing`, ещё `ACTIVE` -> `closed_take`), рядом записываются `slippage_bp` / `slippage_r` и модельная цена.
- OCO эмулируется мониторингом: после каждого закрытия обе ноги ставятся в очередь и через `oco_check_delay_seconds` проверяются через `GetStopOrders` / `GetOrders`. Уцелевшая нога отменяется вручную с критическим алертом `OCO monitoring:` (не более `oco_check_attempts` попыток).
- Инварианты защиты проверяются каждые `broker_stop_verify_interval_seconds`: открытая позиция без `broker_stop_id` либо с id, которого нет в активном списке брокера, даёт критический `Invariant violation:` (сбой постановки — `protection_failed`) и повторную постановку с экспоненциальным backoff от `protection_retry_seconds`.
- SIGTERM/SIGINT запрашивает очистку. Ожидающие входные заявки отменяются всегда, стопы — через `cancel_stop_order()`; открытые позиции закрываются только при `close_positions_on_shutdown=true`. При значении false по умолчанию позиции остаются открытыми и сохраняют брокерскую защиту (задача #174).
- Зачистка сиротских стопов (задача #199, §46.12): раз в `orphan_stop_sweep_interval_seconds` исполнитель сверяет `GetStopOrders(active)` с `live_positions` и снимает ACTIVE SELL-стопы, на которые не ссылается ни одна строка, которые входят в настроенный универсум, на которые не претендуют ни OCO-, ни amend-проход, по которым брокер не держит позицию и которые старше `orphan_stop_grace_seconds`. Два прохода подряд должны сойтись во мнении, за проход снимается не более `orphan_stop_max_cancels` стопов, а превышение потолка переводит проход в fail-closed (не снимается ничего; `orphan_sweep_fail_closed_total` + прореженный critical-алерт). Сохранённые стопы логируются `orphan_stop_kept ... reason=<code>` на DEBUG, снятые — `orphan_stop_cancelled stop_order_id=... reason=orphaned`, счётчики лежат в `protection.*` на `/api/live-trading/metrics`.

- Причины отказа BUY читайте в `reports/live-executor/executor.log`. Каждая запись `Live signal skipped` содержит `ticker=<тикер>`, стабильный `reason=<код>` и релевантные значения. Ожидаемые коды фильтров и лимитов: `outside_entry_window`, `stale_or_missing_orderbook`, `imbalance_below_threshold`, `insufficient_cash`, `invalid_stop`, `insufficient_capital`, `max_open_positions` и `broker_error`; `min_lot` по контракту sizing остаётся исполнимым. Например, `reason=imbalance_below_threshold imbalance=0.9 imbalance_threshold=1.0` означает, что стрим работает, но фильтр отклонил вход, а `reason=stale_or_missing_orderbook orderbook_age_seconds=missing` — что данных стакана нет. Эти записи появляются только после BUY-решения от `StrategyEvaluator`; отсутствие записей об отказах может означать, что BUY-сигналов не было. Для broker errors логируются только операция и тип исключения, без реквизитов счёта, credentials и текста исключения.
- Диагностика: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Тесты: `cd backend && python -m pytest -q tests/test_live_executor.py tests/test_moex_session.py`.

## 33. Эксплуатация sandbox LiveExecutor на `test_20260830_new_level` (задача #135)

Явное решение PO поверх вердикта #130 «не paper» для **другой** Lab-строки: `test_20260830_new_level` (`levels_sr_support` + `signal_4h_buy`, RR 1:3). Это не published C (RR 1:2) и не задача #77 (`test_20260731` + top-5 из #66).

1. Ровно одна locked paper-стратегия: `test_20260830_new_level`. `test_20260731` оставить разблокированной; её конфиг не перезаписывать. Открытая paper-позиция FEES на старом имени остаётся под монитор.
2. Пересобрать: `docker compose up -d --build backend`. `/health` должен быть `ok`.
3. Paper-стек должен покрывать понедельничную сессию с запасом. Не останавливать paper ради live. Streaming/refresh покрывает top-15 ∪ LIVE_UNIVERSE. `trading.trading_universe` не сужать.
4. Preflight в сессию MOEX (в воскресенье стаканы будут stale):
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   Проверка падает, если backend нездоров, `LIVE_UNIVERSE` не равен 12 PO-именам, locked-стратегия не одна и не `test_20260830_new_level`, нет свободных sandbox RUB, хотя бы один из 12 стаканов старше пяти минут, paper-процессы запущены не в единственном экземпляре, во вселенной БД не 15 строк или `allow_real_trading` не равен false.
5. Overnight sandbox-день (задача #137), после пересборки backend:
   `START_LIVE_EXECUTOR=1 ./start_processes.sh`
   Не задавать `DURATION_MINUTES`. Запуск в воскресенье вечером; LiveExecutor ждёт понедельника 10:00 МСК. Если paper уже жив и покрывает понедельник 19:00: `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 ./start_processes.sh`.
6. Leftover canary RUAL уже `closed_stop`. На старте #135 открытых sandbox-позиций нет.
7. После окна записать в задачи #135 / #137: init-тикеры, числа `reason=`, число BUY, последние `live_positions` и подтверждение, что `paper_equity` писалась. Никогда не включать `allow_real_trading`.
   Исторический canary с фиксированным окном по-прежнему `DURATION_MINUTES=60` (handover §19).

## 43. Эксплуатация живучести LiveExecutor (задача #174)

Завершена 2026-09-19. Живучесть цикла, безопасный останов, блокировка на выделенном соединении, пульс и метрики.

### Зачем

`LiveExecutor` мог погибнуть от одной транзиентной ошибки (таймаут БД, сбой gRPC брокера), оставляя позиции без защиты. Блокировка `pg_try_advisory_lock(151001)` выполнялась на пуловом соединении и могла «утечь», если `shutdown()` использовал другое пуловое соединение. При останове с `close_positions_on_shutdown=false` стоп/тейк-ордера отменялись, оставляя позиции без защиты между перезапусками исполнителя.

### Что изменилось

| Файл | Изменения |
| --- | --- |
| `backend/app/db/db_manager.py` | Новые методы: `get_dedicated_connection()`, `release_dedicated_connection(conn)`, контекстный менеджер `dedicated_connection()`. Существующие потребители (`select`, `execute`, `insert`) не изменены. |
| `backend/app/analytics/live_executor.py` | Блокировка на выделенном соединении (`_lock_conn`); защита цикла с `MAX_CONSECUTIVE_ERRORS = 5`; изоляция на уровне единицы работы в `process_latest_bars()`, `refresh_contexts()`, `monitor_positions()`; безопасный останов оставляет открытые позиции защищёнными; метрики пульса (`heartbeat_ts`, `iterations_total`, `errors_total`, `errors_consecutive`, `last_error_at`); новый метод `get_metrics()` для внешнего мониторинга (задача E #177). |
| `backend/tests/test_live_executor.py` | 10 новых тестов: блокировка, защита цикла, изоляция, безопасный останов, пульс. Итого: 78 тестов. |

### Поведение

- **Защита цикла**: `monitor_positions()` и `process_latest_bars()` обёрнуты в `try/except` внутри `run()`. Каждая ошибка инкрементирует `_consecutive_errors`; успех сбрасывает его в 0. При `MAX_CONSECUTIVE_ERRORS = 5` последовательных ошибках пишется критический алерт и исполнитель корректно останавливается.
- **Изоляция на уровне единицы работы**: ошибки по одному тикеру (в `process_latest_bars()` / `refresh_contexts()`) или по одной позиции (в `monitor_positions()`) логируются с `exc_info=True` и не прерывают обработку остальных единиц.
- **Безопасный останов**: при `close_positions_on_shutdown=false` (дефолт) `shutdown()` **не** отменяет стоп/тейк-ордера для открытых позиций и **не** обнуляет `broker_stop_id` / `broker_take_id` в БД. Отменяются только ожидающие входные ордера (`pending`). Открытые позиции переживают SIGTERM/SIGINT с защитой на стороне брокера. При `close_positions_on_shutdown=true` поведение без изменений (позиции закрываются).
- **Блокировка**: выполняется на выделенном соединении (`get_dedicated_connection()`) в `initialize()`, освобождается на **том же** соединении в `shutdown()`. Соединение удерживается вне авто-возврата пула на протяжении всей жизни процесса.
- **Пульс**: `heartbeat_ts` и `iterations_total` обновляются после каждой успешной итерации цикла. `errors_total` и `last_error_at` обновляются при каждой ошибке. `get_metrics()` возвращает `{"heartbeat_ts", "iterations_total", "errors_total", "errors_consecutive", "last_error_at"}` (с #176 — плюс риск-поля, с #177 — счётчики алертинга и окна heartbeat; снимок персистится и отдаётся через `GET /api/live-trading/metrics`, см. §45).

### Команды

```bash
# Юнит-тесты (78 тестов)
cd backend && python -m pytest tests/test_live_executor.py -q

# Проверка удержания блокировки
psql -c "SELECT pg_try_advisory_lock(151001);"  # возвращает false, если исполнитель запущен
```

### Известные ограничения

- ~~`MAX_CONSECUTIVE_ERRORS = 5` — константа модуля~~ **закрыто в #177**: порог стал ключом `LIVE_ALERTING.max_consecutive_errors` (env `LIVE_MAX_CONSECUTIVE_ERRORS`, диапазон `[1, 100]`), константа модуля осталась только fallback-дефолтом. См. §45.
- ~~Пульс хранится только в памяти~~ **закрыто в #177**: снимок `get_metrics()` персистится в `trading.app_settings['live_executor_metrics']` (JSONB) и читается через `GET /api/live-trading/metrics`, который сам считает возраст пульса и флаг `stale`. См. §45.

### SSL-сертификаты для T-Bank gRPC

T-Bank Invest API требует корневой сертификат для gRPC-подключений. На хосте Windows Python/gRPC не находит системные сертификаты автоматически, поэтому используется переменная окружения:

```bash
export GRPC_DEFAULT_SSL_ROOTS_FILE_PATH="$(pwd)/backend/certs/tbank-root.pem"
```

Этот путь автоматически устанавливается в `start_processes.sh`. Если сертификат отсутствует или невалиден, `TinkoffSandboxClient.check_balance()` упадёт с `CERTIFICATE_VERIFY_FAILED`.

**Диагностика:**
```bash
cd backend
python -c "from app.broker.tinkoff_sandbox import TinkoffSandboxClient; print(TinkoffSandboxClient().check_balance())"
```

Если вернётся баланс (число) — сертификат работает. Если `CERTIFICATE_VERIFY_FAILED` — проверьте, что `backend/certs/tbank-root.pem` существует и содержит валидный PEM-сертификат.


### Проверка механизма защиты #175 (2026-09-26)

**Результат:** Полный цикл брокерской защиты позиций (#175) проверен прямыми вызовами клиента вне торговой сессии. Все 9 шагов прошли успешно:

1. ✅ Покупка (execute_order buy)
2. ✅ Постановка стопа (post_stop_order с UUID v4)
3. ✅ Проверка стопа (get_stop_orders)
4. ✅ Amend-трейлинг: новый стоп выше (post_stop_order)
5. ✅ Amend-трейлинг: оба стопа видны
6. ✅ Amend-трейлинг: старый стоп отменён (cancel_stop_order)
7. ✅ Amend-трейлинг: остался только новый стоп
8. ✅ OCO-мониторинг: отмена стопа (cancel_stop_order)
9. ✅ Закрытие позиции (execute_order sell)

**Исправленные баги:**
- `uuid5 → uuid4` в `_arm_broker_stop` (ошибка 30028: order_id has invalid UUID format)

**Известные проблемы:**
- `SandboxStopOrderState` имеет поле `lots_requested`, а не `quantity` (может вызвать AttributeError в `_active_stop_ids` или `_reconcile_protection`)

**Статус:** Механизм работоспособен. Требуется финальный smoke в торговой сессии (понедельник 10:00 МСК) для подтверждения полного цикла через LiveExecutor.
