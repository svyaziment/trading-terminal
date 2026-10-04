# Работа с клиентом T-Bank Sandbox

> **Source:** handover.ru.md sections 13, 46
> **Last refreshed:** 2026-10-04, task-346

## 13. Работа с клиентом T-Bank Sandbox

- Точка входа: `app.broker.tinkoff_sandbox.TinkoffSandboxClient`. Всё исполнение брокерских ордеров должно оставаться за этим классом; downstream executor не должен создавать или вызывать production-сервис `orders`.
- Обязательное окружение: отдельный sandbox-токен `TINVEST_SANDBOX`. Необязательный `TINVEST_SANDBOX_ACC` фиксирует sandbox-счёт; иначе выбирается первый открытый. Клиент намеренно никогда не использует реквизиты рыночных данных `TINVEST_TOKEN` / `TINVEST_ACC`. Обнаружение счёта не открывает и не пополняет его.
- Read-only smoke check:
  `cd backend && python -c "from app.broker.tinkoff_sandbox import TinkoffSandboxClient; print(TinkoffSandboxClient().check_balance())"`
- Market-ордер: передайте `instrument_id`, положительный целый `quantity` в лотах и при необходимости `direction` (`buy`/`sell`). `price` передавать нельзя.
- Limit-ордер: передайте те же поля, а также `order_type="limit"` и положительный `price`. В `instrument_id` используйте UID/FIGI инструмента, который принимает T-Bank.
- Для отмены нужен брокерский `order_id`, возвращённый `execute_order`.
- Retry-политика берётся только из `SANDBOX_TRADING` в `trading_config.py`. Не добавляйте отдельные retry-циклы вокруг `execute_order`: клиент уже повторяет временные gRPC-ошибки с тем же idempotency key.
- Клиент не открывает sandbox-счёт и не зачисляет на него 50 000 RUB из эпика автоматически. Создание и пополнение — явная операция пользователя. Никогда не печатайте токены и не коммитьте `.env`.
- Unit-тест: `cd backend && python -m pytest -q tests/test_tinkoff_sandbox.py`.

## 46. Реальный контур T-Bank, деплой-миграции и глобальный аварийный останов (задача #178)

**Что изменилось.** Live-контур перестал быть песочничным по построению: исполнитель
выбирает брокерский клиент через фабрику, миграции стали явным шагом деплоя, а у
оператора появился глобальный аварийный останов входов. Английский оригинал:
`handover.md` §46; архитектура — `project-context.ru.md` §24.

### 46.1 Три пары учётных данных (строго разделены)

| Назначение | Token | Счёт | Кто читает |
|---|---|---|---|
| Market data (свечи, стакан) | `TINVEST_TOKEN` | `TINVEST_ACC` | `data_loader`, `online_data` |
| Исполнение в песочнице | `TINVEST_SANDBOX` | `TINVEST_SANDBOX_ACC` | `TinkoffSandboxClient` |
| Исполнение на реальном счёте | `TINVEST_LIVE_TOKEN` | `TINVEST_LIVE_ACC` | `TinkoffLiveClient` |

Fallback между парами запрещён кодом: `TinkoffLiveClient` падает с
`LiveConfigurationError`, если `TINVEST_LIVE_TOKEN` пуст **или совпадает** с
`TINVEST_TOKEN` (защита от заполненной не той переменной). С Issue #192 у второго
отказа есть явный opt-out для деплоя, который намеренно использует ОДИН физический
токен и для market data, и для реального счёта: `ALLOW_LIVE_TOKEN_REUSE=true`
(по умолчанию `false`, в коде `False`, парсится строго как `ALLOW_REAL_TRADING`).
Клиент тогда строится и пишет WARNING: ротация такого токена меняет оба контура
сразу. Opt-in не вводит fallback — каждый клиент по-прежнему читает только свою
переменную. Токен не логируется;
счёт логируется маскированным (`***1234`). `TINVEST_LIVE_ACC` может быть пустым —
тогда берётся первый открытый счёт из `users.get_accounts()` (при нескольких
открытых счетах пишется WARNING с рекомендацией зафиксировать счёт явно).

### 46.2 Как выбирается контур

Единственный источник истины — `SANDBOX_TRADING.allow_real_trading`
(`backend/app/analytics/trading_config.py`), в коде всегда `False` (красная линия
эпика #172). Переопределение — только env `ALLOW_REAL_TRADING`: принимаются слова
`1/true/yes/on` и `0/false/no/off`, anything else → `ValueError` на старте
(опечатка `ture` не может тихо означать «песочница» или «реал»).

`app/broker/client_factory.create_execution_client()`:

- gate закрыт → `TinkoffSandboxClient`, лог INFO `Using TinkoffSandboxClient: T-Bank sandbox contour`;
- gate открыт → `TinkoffLiveClient`, лог **WARNING** `Using TinkoffLiveClient: REAL T-Bank account contour`.

Выбранный контур виден оператору в трёх местах: `broker_contour` в снимке метрик
(секция `source` ответа `GET /api/live-trading/metrics`), заголовки алертов
`live_start` / `live_entry` / `live_exit` («песочница» / «реальный счёт») и поле
«Контур брокера» в алерте старта. При `ALLOW_REAL_TRADING=true` sandbox-клиент
сознательно отказывается конструироваться — смешать контуры в одном процессе нельзя.

### 46.3 Runbook перехода на реальный счёт

Порядок обязателен: каждый шаг проверяется до следующего.

1. **Песочница зелёная.** Прогон `LiveExecutor` в песочнице без ошибок защиты
   (`protection_failed_total == 0`, `invariant_violations_total == 0`), preflight
   `ok=true`:
   ```bash
   docker compose exec -T backend python -m app.analytics.live_executor_preflight
   ```
2. **Миграции применены.** `alembic current` показывает `20260928_001 (head)`:
   ```bash
   docker compose run --rm migrate alembic current
   ```
3. **Учётные данные реального контура** в `.env` (файл вне git):
   `TINVEST_LIVE_TOKEN`, `TINVEST_LIVE_ACC` (рекомендуется явно),
   `ALLOW_REAL_TRADING=false` — пока не заполнены оба предыдущих пункта.
4. **Проверка учётных данных без торговли.** Временный запуск preflight с
   ожиданием реального контура:
   ```bash
   ALLOW_REAL_TRADING=true PREFLIGHT_EXPECT_CONTOUR=real \
     docker compose run --rm -e ALLOW_REAL_TRADING -e PREFLIGHT_EXPECT_CONTOUR \
     -e TINVEST_LIVE_TOKEN -e TINVEST_LIVE_ACC migrate \
     python -m app.analytics.live_executor_preflight
   ```
   Ожидание: `contour=real`, `contour_matches_expectation=true`,
   `sandbox_free_rub > 0` (это свободные деньги **реального** счёта),
   `live_positions_schema=true`.
5. **Риск-лимиты под реальный капитал.** `MAX_POSITION_SIZE`, `MAX_DAILY_LOSS_PCT`,
   `MAX_OPEN_POSITIONS` — в `.env`; значения видны в
   `GET /api/live-trading/equity/latest` → `risk.limits`.
6. **Включение.** `ALLOW_REAL_TRADING=true` в `.env` → пересборка и перезапуск:
   ```bash
   docker compose up -d --build backend
   START_LIVE_EXECUTOR=1 ./start_processes.sh
   ```
7. **Контроль первого цикла.** В логе исполнителя должна быть строка WARNING
   `Using TinkoffLiveClient`, в алерте `live_start` — «Контур брокера: real»,
   в `GET /api/live-trading/metrics` → `source.broker_contour == "real"`.
8. **Первая сделка — под наблюдением.** После первого `live_entry` проверить, что
   `protection.stops_armed_total` растёт и `positions.unprotected_total == 0`.

Откат включения: `ALLOW_REAL_TRADING=false` → `docker compose up -d --build backend`.
Открытые позиции реального счёта при этом **не закрываются автоматически**
(`close_positions_on_shutdown=false`): их брокерские стопы остаются выставленными,
дальше их ведёт либо restarted-исполнитель, либо оператор вручную.


### 46.4 Глобальный аварийный останов (kill switch)

**Что это.** Один булев ключ `trading.app_settings.live_kill_switch`
(миграция `20260928_001`). `true` — исполнитель отклоняет **каждый новый вход**
с причиной `kill_switch`; `false` — входы разрешены.

**Чего он НЕ делает** (красные линии эпика #172):

- не закрывает открытые позиции (нет auto-flatten);
- не снимает и не отменяет брокерские стопы — защита позиций сохраняется;
- не трогает paper-контур и трейлинг-переключатель `trailing_kill_switch`
  (это отдельный рычаг: он останавливает переносы стопов, а не входы).

**Как включить / выключить.**

```bash
# Включить (аварийный останов входов)
curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true, "reason": "аномальная волатильность"}'

# Выключить
curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
  -H 'Content-Type: application/json' -d '{"enabled": false}'

# То же самое SQL-ом (эндпоинт — не единственная дверь)
docker compose exec -T backend python -c "from app.db.db_manager import DBManager; \
DBManager().execute(\"UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() WHERE key='live_kill_switch'\")"
```

Ответ эндпоинта: `ok`/`confirmed` — **подтверждение чтением обратно**; если строку
не удалось прочитать после записи, `ok=false` (неподтверждённый аварийный останов
не выдаётся за успех). Недоступная `trading.app_settings` → `503` с именем
миграции. `reason` (до 200 символов) уходит в аудит-лог контейнера и в ответ,
в БД не хранится.

**Задержка срабатывания.** Исполнитель перечитывает ключ каждый цикл
(`LIVE_TRADING.check_interval_seconds`, по умолчанию 30 с) — перезапуск процесса
не нужен.

**Fail-safe (решение D2).** Отсутствие строки, `NULL` или ошибка чтения БД
трактуются как **ВКЛЮЧЕНО**: исполнитель, который не может прочитать свой
аварийный останов, входы не открывает. In-memory дефолт
`LIVE_TRADING['live_kill_switch']` тоже `true`, а `initialize()` читает
сохранённое значение до первого цикла — поэтому рестарт на мигрированной БД не
выглядит «переходом» и не шлёт лишний алерт.

**Алерты.** Переходы публикуются в Telegram под существующими ключами
`kill_switch_on` / `kill_switch_off` (critical только на включение), заголовок —
«Глобальный kill switch ВКЛЮЧЁН/ВЫКЛЮЧЕН», в поле «Источник» — происхождение
значения (`app_settings`, `app_settings:missing_key`, `app_settings:null_value`,
`db_error:<тип>`). Порядок в потоке: сначала трейлинг-переключатель (как до #178),
затем глобальный. Снимок метрик пишется сразу после перехода (решение D5).

**Где видно состояние.**

```bash
curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool
```

- `global_kill_switch.active` — что применит исполнитель (fail-safe при отсутствии строки);
- `global_kill_switch.found` / `.reason` — значение из строки или из правила fail-safe
  (`missing_row` / `unreadable_row`);
- `global_kill_switch.live_active` против `.snapshot_active` — строка БД против последнего
  снимка (расхождение видно, а не сглаживается);
- `global_kill_switch.rejections_total` — сколько сигналов отклонено;
- `kill_switch.*` — независимо трейлинг-переключатель;
- `state` = `kill_switch`, если включён любой из двух.


### 46.5 Деплой: миграции как явный шаг

```bash
docker compose up -d --build backend   # соберёт образ, выполнит migrate, затем поднимет API
docker compose run --rm migrate        # только миграции
docker compose logs migrate --tail 50  # что применилось
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic history --verbose
```

- Сервис `migrate` — one-shot (`command: ["alembic","upgrade","head"]`,
  `restart: "no"`), а `backend.depends_on.migrate.condition =
  service_completed_successfully`: упавшая миграция останавливает деплой, а не
  проявляется в бою (`assert_live_schema` всё равно abort-нул бы исполнитель).
- Образ содержит `alembic.ini` и `alembic/` (`COPY` в `backend/Dockerfile`) —
  до #178 их в образе не было.
- `migrate` и `backend` используют **один** env-блок (YAML-anchor
  `x-backend-env`), поэтому DSN миграций и приложения не может разъехаться.
- `alembic/env.py` берёт URL из `app.core.config.get_app_database_url()`. С #178
  эта функция читает пароль как `POSTGRES_PASSWORD` → `PSTGRS_PWD` → `app`;
  раньше `PSTGRS_PWD` (то, что реально передаёт compose) игнорировался, и
  миграции в контейнере падали на аутентификации.
- Автомиграций в коде приложения нет (решение D4): runtime-DDL
  `ensure_live_runtime_schema()` остаётся страховкой для standalone-запуска и
  идемпотентно сходится к той же форме.

### 46.6 Откат

```bash
docker compose run --rm migrate alembic downgrade -1     # 20260928_001 -> 20260927_001
docker compose run --rm migrate alembic downgrade 20260927_001
```

Откат `20260928_001` удаляет **только** строку `live_kill_switch`
(`DELETE FROM trading.app_settings WHERE key='live_kill_switch'`); таблицы и
данные не трогаются. Важно: удалённая строка читается как ВКЛЮЧЕНО (fail-safe),
поэтому откат миграции **блокирует входы**, а не разрешает их. Полный откат фичи —
предыдущий образ + отсутствие `ALLOW_REAL_TRADING` в `.env`.

### 46.7 Диагностика

| Симптом | Причина | Действие |
|---|---|---|
| `LiveConfigurationError: TINVEST_LIVE_TOKEN is empty` | gate открыт, токена нет | заполнить `.env` или вернуть `ALLOW_REAL_TRADING=false` |
| `... must not reuse the market-data TINVEST_TOKEN` | один токен в двух переменных | осознанная схема: `ALLOW_LIVE_TOKEN_REUSE=true`; иначе проверить, какой токен выдан в кабинете T-Bank |
| `LiveAPIError: T-Bank live <method> failed`, в логе `TypeError ... unexpected keyword argument` | форма вызова разошлась с установленным SDK | `192-callshape-check.py` (Issue #192); `t-tech-investments` запинен на `1.51.0` |
| `migrate` завершается с кодом 1 и `No module named 'psycopg'` | SQLAlchemy 2.1 сделал psycopg3 драйвером по умолчанию для «голого» `postgresql://` | держать пин `sqlalchemy<2.1` (Issue #192) или поставить `psycopg[binary]` |
| `Refusing to build a real-money client` | `ALLOW_REAL_TRADING` не дошёл до контейнера | `docker compose exec backend env \| grep ALLOW_REAL` |
| Входы не открываются, `reason=kill_switch`, `found=false` | нет строки `live_kill_switch` | `docker compose run --rm migrate` |
| `alembic` не найден в контейнере | старый образ | `docker compose up -d --build backend` |
| Миграция падает на auth | пароль не доехал | `PSTGRS_PWD` / `POSTGRES_PASSWORD` в `.env` |

### 46.8 Известные ограничения

- `GetStopOrders` реального контура не имеет фильтра по датам
  (`GetStopOrdersRequest` = `account_id` + `status`), поэтому `from_date`/`to_date`
  в `TinkoffLiveClient.get_stop_orders()` приняты для паритета сигнатуры и
  игнорируются; фильтрация по инструменту — клиентская (uid / FIGI / ticker).
- Idempotency-ключ реальной заявки передаётся как `idempotence_id`
  (`order_id` в реальном API — биржевой номер заявки).
- Discovery реального счёта берёт первый открытый счёт; при нескольких открытых
  (брокерский + ИИС) счёт нужно фиксировать `TINVEST_LIVE_ACC` явно.
- `live_kill_switch` блокирует только **входы**. Выходы, трейлинг, OCO-мониторинг
  и сверка по филлам продолжают работать — сознательно: останов не должен
  оставлять позицию без защиты.
- Эндпоинт kill switch не аутентифицирован (как и весь API терминала): он
  рассчитан на локальный/доверенный контур.
- Preflight-проверка `real_trading_disabled` заменена на
  `contour_matches_expectation` + `PREFLIGHT_EXPECT_CONTOUR`; старые чек-листы,
  ссылающиеся на прежнее имя ключа, нужно обновить.

### 46.9 Тесты

```bash
cd backend
python -m pytest tests/test_tinkoff_live.py -q        # реальный клиент, gate, фабрика (51)
python -m pytest tests/test_live_kill_switch.py -q    # миграция, гейт, fail-safe, API (41)
python -m pytest tests/test_deploy_migrations.py -q   # цепочка alembic, Dockerfile, compose, DSN (15)
```


### 46.10 Read-only верификация реального контура (задача #192)

Две диагностики лежат вместе с рабочими артефактами в
`reports/190-production-trading-infrastructure/192-g1-production-client-verify/`
(в образ не входят и в торговый путь не попадают):

- **`192-contract-check.py`** — контракт безопасности брокерского слоя,
  проверяется без учётных данных и без единого сетевого вызова. Проверки:
  глобальный гейт закрыт, `create_execution_client()` по умолчанию возвращает
  песочничный клиент, принудительная сборка реального клиента падает fail-closed,
  паритет методов и именованных аргументов live/sandbox, классификация методов
  «мутирующие / только чтение», типы ошибок live наследуют песочничные.
  Вердикт `CONTRACT_OK`, код возврата `0`.
- **`192-live-smoke.py`** — read-only smoke реального контура. Глобальный
  `ALLOW_REAL_TRADING` не переключается: клиент собирается диагностическим
  аргументом конструктора `allow_real_trading=True`, после чего каждый
  мутирующий метод (`execute_order`, `cancel_order`, `post_stop_order`,
  `cancel_stop_order`) затеняется на экземпляре поднимающей исключение заглушкой.
  Установка заглушек проверяется по маркерному атрибуту, **а не** вызовом метода:
  call-probe на реальном контуре сам был бы мутирующим запросом. Вызываются
  только read-only API: `users.get_accounts`, `check_balance`, `get_positions`,
  `get_orders`, `get_stop_orders(status="active")`,
  `get_operations(state="executed", 7 дней)`.
  `--self-test` прогоняет ту же логику на внутрипроцессном fake-клиенте
  (без учётных данных и сети) и дополнительно делает call-probe заглушек там.
  Коды возврата: `0` — зелёный smoke / self-test, `1` — провал, `3` — заблокировано,
  потому что `TINVEST_LIVE_TOKEN` отсутствует.

Порядок запуска (из папки задачи; артефакты создаются внутри контейнера и
копируются обратно через `docker compose cp`, что сохраняет UTF-8):

```bash
cd reports/190-production-trading-infrastructure/192-g1-production-client-verify
docker compose cp 192-contract-check.py backend:/tmp/192-contract-check.py
docker compose cp 192-live-smoke.py backend:/tmp/192-live-smoke.py

docker compose exec -T backend sh -c \
  'python /tmp/192-contract-check.py > /tmp/contract-check.txt 2>&1; echo exit=$?'
docker compose exec -T backend python /tmp/192-live-smoke.py --self-test
docker compose exec -T -e TINVEST_LIVE_TOKEN -e TINVEST_LIVE_ACC backend \
  python /tmp/192-live-smoke.py --json /tmp/smoke-real.json
```

Правила маскирования артефактов: токены показываются только как наличие и длина
(`set(len=88)` / `(empty)` / `<not-set>`), без префикса; идентификаторы счетов
сокращаются до последних четырёх символов (`***7890`).

Статус на 2026-09-29: `contract-check.txt` = 9/9 зелёных,
`smoke-self-test.txt` = `SELF_TEST_OK` (9 read-only шагов, `orders_placed=0`),
аутентифицированный прогон — `BLOCKED_NO_CREDENTIALS` (код `3`), потому что
`TINVEST_LIVE_TOKEN` в этом окружении отсутствует. Preflight-проверка
(`192-preflight-check.py` с `PREFLIGHT_EXPECT_CONTOUR=real`) подтвердила
ожидаемое fail-closed поведение:
`contour_now=sandbox`, `sandbox_gate=true`, `real_gate=false`,
`real_expectation_passes=false`, вердикт `FAIL_CLOSED_OK` (exit `0`) — путь
продуктовой миграции из шага 3 §46.3 не стартует, пока гейт закрыт и live-токена
нет. На биржу не отправлено ничего: реальный контур поднимался только для чтения
балансов, позиций и истории.


### 46.11 Остановка потока без flatten-all: три рычага, runbook, drill (задача #193)

**Зачем этот раздел.** Эпик #190 сохраняет решение PO: **flatten-all запрещён**.
Остановка потока не имеет права ничего продавать. Рычагов три, действуют они
по-разному, и на реальных деньгах разница между «управляемым остановом» и «голой
позицией» — это ровно тот рычаг, который дёрнули. Нового кода здесь нет:
механизмы поставлены в #174 (SIGTERM/shutdown), #151 (трейлинг-переключатель) и
#178 (глобальный переключатель). Этот раздел — матрица, операционный runbook и
drill, который все три доказывает.

**Матрица трёх рычагов.**

| | Рычаг 1: глобальный kill switch | Рычаг 2: трейлинг kill switch | Рычаг 3: SIGTERM / SIGINT |
|---|---|---|---|
| Механизм | `trading.app_settings.live_kill_switch` (#178, миграция `20260928_001`) | `trading.app_settings.trailing_kill_switch` (#151) | сигнал процессу: `stop_processes.sh`, `docker compose stop backend`, Ctrl+C |
| Как включить | `POST /api/live-trading/kill-switch {"enabled": true, "reason": "..."}` или SQL | **только SQL — API-эндпоинта нет** (§41) | `./stop_processes.sh` (SIGTERM исполнителю) |
| Задержка | ≤ `check_interval_seconds` (30 с): перечитывается каждый цикл, рестарт не нужен | ≤ 30 с: перечитывается каждый цикл, рестарт не нужен | мгновенно: обработчик взводит `shutdown_requested`, очистка — в `shutdown()` |
| Новые входы | **блокируются** — причина пропуска `kill_switch`, счётчик `kill_switch_rejections_total`; проверка идёт до сессионного окна, стакана, сайзинга и любого брокерского вызова | разрешены — этот рычаг входов не касается | отсутствуют: процесса нет |
| Трейлинг-храповик | продолжает работать | **заморожен**: нет арминга, нет переноса, нет брокерского amend | останавливается вместе с процессом |
| Открытые позиции | не трогаются | не трогаются | не трогаются — **flatten отсутствует** (`close_positions_on_shutdown=false`) |
| Брокерские стопы | остаются выставленными | остаются выставленными | остаются выставленными; отменяются только **pending-заявки на вход**, их строки помечаются `cancelled` (причина `shutdown`) |
| Fail-safe | нет строки / `NULL` / ошибка БД → **ВКЛЮЧЕНО** (`app_settings:missing_key`, `app_settings:null_value`, `db_error:<тип>`) | нет строки → **False** (исторический fail-open); ошибка БД → **True** (fail-safe) | n/a |
| Нужен ли рестарт | нет | нет | `./start_processes.sh` при `START_LIVE_EXECUTOR=1`; состояние восстанавливается из БД |
| Где проверить | `global_kill_switch.*` в `GET /api/live-trading/metrics`; аудит-строка `Global live kill switch set to ON (confirmed=... reason=...)`; Telegram `kill_switch_on` | `kill_switch.*` в `/metrics`; Telegram `kill_switch_on`; §41 | строка лога `Position <id> left protected with broker_stop_id=...`; `positions.protected_total` / `unprotected_total` в `/metrics` |

Отменяет что-либо только рычаг 3, и только pending-входы. Рычаги 1-2 не
отменяют ничего: они меняют то, что циклу разрешено сделать следующим, и оба
перечитываются каждый цикл. Единственная настройка, при которой shutdown
закрывает позиции, — `close_positions_on_shutdown=true`; проект её не поставляет,
а эпик #190 её запрещает.

**Runbook A — штатная остановка потока, позиции остаются открытыми.**

1. Блокируем новые входы:
   ```bash
   curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
     -H 'Content-Type: application/json' \
     -d '{"enabled": true, "reason": "плановая остановка 2026-09-30"}'
   ```
   В ответе обязаны быть `"ok": true, "confirmed": true` — `confirmed` это
   чтение строки обратно. `ok=false` означает, что запись не легла: продолжать
   нельзя.
2. Проверяем: `curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool`
   → `global_kill_switch.active=true`, `state="kill_switch"`; в логе контейнера
   есть `Global live kill switch set to ON (confirmed=True reason=...)`;
   в Telegram пришёл `kill_switch_on`.
3. По желанию — дополнительно морозим храповик (только SQL):
   ```sql
   UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now()
   WHERE key='trailing_kill_switch';
   ```
4. Ждём один цикл (≤ 30 с), чтобы исполнитель закончил уже начатую работу.
5. Останавливаем процесс: `./stop_processes.sh` (SIGTERM). Никаких
   `docker compose kill` и `kill -9`: SIGKILL пропускает `shutdown()`, поэтому
   resting-заявки на вход останутся у брокера, а финальный снимок метрик не
   будет записан.
6. Проверяем, что защита уцелела:
   - лог: по одной строке `Position <id> left protected with broker_stop_id=... broker_take_id=...`
     на каждую открытую позицию;
   - `/api/live-trading/metrics` → `positions.open_total == positions.protected_total`,
     `unprotected_total = 0`, `unprotected_tickers = []`;
   - строки `trading.live_positions` не изменились: `status='open'`, те же
     `broker_stop_id` / `broker_take_id`, тот же `updated_at`;
   - стопы живы у брокера — read-only `get_stop_orders(status='active')`
     (`193-shutdown-drill.py` делает ровно это до и после сигнала).
7. Только после этого закрываем позиции руками в приложении брокера, если нужно.

**Runbook B — аварийно: остановить входы, процесс оставить работать.**

Применяется, когда контур ведёт себя нештатно, но позиции должны оставаться
защищёнными и управляемыми (стопы продолжают срабатывать, храповик работает, если
рычаг 2 не взведён): шаги 1-2 Runbook A, и больше ничего. Снятие —
`{"enabled": false, "reason": "..."}` с проверкой
`global_kill_switch.active=false` и алерта `kill_switch_off`. Пока переключатель
включён, каждый отклонённый сигнал считается
(`global_kill_switch.rejections_total`) и логируется как
`reason=kill_switch source=<происхождение>`.

**Runbook C — реальный контур: порядок действий перед ручным закрытием позиции.**

Решение PO прямое: без flatten-all, закрытие руками через приложение брокера.
Порядок важен, потому что работающий исполнитель реконсилирует всё, что видит у
брокера:

1. Рычаг 1 ВКЛ (входы заблокированы) — чтобы исполнитель не открыл новую позицию,
   пока вы работаете в приложении брокера.
2. По желанию рычаг 2 ВКЛ — чтобы стопы не двигались у вас под руками.
3. Рычаг 3: `./stop_processes.sh`. На реальном контуре брокерский стоп живёт
   независимо от процесса: позиция остаётся защищённой, пока ничего не
   отправляется.
4. Проверяем по шагу 6 Runbook A. На реальном контуре у `GetStopOrders` нет
   фильтра по датам (§46.8) — читайте список идентификаторов, а не угадывайте.
5. Закрываем позицию вручную в приложении брокера.
6. На следующем запуске исполнитель выполняет реконсиляцию: исчезнувшая позиция
   закрывается реальным филлом из `GetOperations`, а оставшийся парный ордер
   (стоп или тейк) отменяется. **Классификацию читайте правильно**: при всё ещё
   `ACTIVE` стопе ручное закрытие записывается как `closed_take`, если тейк-ордер
   существовал, и как `closed_broker` в противном случае
   (`_classify_exit_reason`); причины «manual» в замороженном списке статусов #173
   нет, поэтому доверять надо `exit_price_actual` / `lots_executed` из филла, а не
   слову в `exit_reason`. Неоднозначный случай логируется как
   `exit_reason_ambiguous position_id=...`.
7. Снимаем рычаг 1 только когда контур снова под исполнителем.

**Drill (задача #193).** `193-shutdown-drill.py` лежит вместе с артефактами в
`reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten/`
(в образ не входит, в торговый путь не попадает). Он прогоняет все три рычага и
**по построению песочничный**: если `create_execution_client()` разрешается в
реальный контур, drill останавливается с `DRILL_BLOCKED` (exit 3), ничего не
успев сделать. Каждый мутирующий метод брокера затеняется считающей заглушкой,
установка которой проверяется по маркеру (без call-probe), единственная запись в
БД — строка `live_kill_switch` в round-trip (восстанавливается в `finally`, с
SQL-страховкой), а `_flush_metrics` на фазе shutdown заменяется регистратором,
чтобы drill не перезаписал снимок `live_executor_metrics`, который отдаёт панель.

Фазы: `environment` (поставляемая политика + заглушки) → `kill_switch_roundtrip`
→ `audit_line` → `entry_gate` (с негативным контролем при выключенном рычаге) →
`fail_safe` → `trailing_lever` → `stop_liveness_before` → `sigterm_shutdown`
(настоящий SIGTERM самому процессу drill, затем `shutdown()` ровно так, как его
вызывает `run()`) → `stop_liveness_after` → `flatten_contrast` (только в
`--self-test`). Коды возврата: `0` `DRILL_OK` / `1` `DRILL_FAIL` / `3`
`DRILL_BLOCKED`. Drill также fail-closed при наличии `pending`-строки:
`shutdown()` отменил бы эту заявку у брокера, а это мутирующее действие.

```bash
cd reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten
docker compose cp 193-shutdown-drill.py backend:/tmp/193-shutdown-drill.py

# герметично: только фейки, без БД / API / брокера — безопасно где угодно
docker compose exec -T backend python /tmp/193-shutdown-drill.py --self-test

# реальный drill на песочнице
docker compose exec -T backend sh -c \
  'python /tmp/193-shutdown-drill.py --json /tmp/drill-193.json > /tmp/drill.txt 2>&1; echo exit=$?'
docker compose cp backend:/tmp/drill.txt      <папка-задачи>/drill-sandbox.txt
docker compose cp backend:/tmp/drill-193.json <папка-задачи>/drill-sandbox.json
```

Статус на 2026-09-30 (песочница; `drill-sandbox.txt`, `drill-sandbox.json`):
`DRILL_OK`, exit `0`, **`mutating calls: 0`**, все четыре заглушки `blocked`.
Round-trip подтвердил включение (`state="kill_switch"`, строка `true`,
`positions.protected_total` не изменился) и вернул базовое `false`; гейт входов
вернул `kill_switch` при включённом рычаге и `unknown_instrument` при выключенном;
аудит-строка содержала `confirmed=True reason=drill-193 audit probe`;
fail-safe матрица воспроизвела все три «включающих» происхождения; настоящий
SIGTERM взвёл `shutdown_requested`, `shutdown()` оставил открытую строку (id 10,
PLZL) побайтово прежней (включая `updated_at`) и записал в лог
`Position 10 left protected with broker_stop_id=01a0df17-...`, а этот стоп был жив
в `GetStopOrders` и до, и после. `--self-test` (`drill-selftest.txt`) также зелёный
и добавляет две ветки, которые песочница показать не могла: pending-строка
отменяется и помечается `cancelled`/`shutdown`, а открытая строка не
записывается вовсе; `close_positions_on_shutdown=true` действительно закрывает
позиции — конфигурация, которую проект не поставляет.

**Находка drill (здесь не исправляется — нужна отдельная issue).** Песочный
`GetStopOrders(active)` вернул 4 стопа при одной открытой позиции: три
идентификатора (`01a0d4fc-1e39...`, `01a0d4fc-7e55...`, `01a0def6-354f...`) не
упоминаются ни одной строкой `live_positions` (`drill-orphan-stops.txt`).
Сиротские sell-стопы — ровно то, вокруг чего `_reconcile_protection` отказывается
перевыставлять защиту, а на реальном контуре это были бы голые ордера. Очистка и
реконсиляция сиротских стопов — отдельная задача: #193 не добавляет кода в
торговый цикл.

**Регрессионные тесты за drill (второй слой доказательств):**
`test_live_executor.py::test_shutdown_leaves_position_protected_by_default`,
`::test_shutdown_cancels_all_pending_orders_without_flattening_by_default`,
`::test_shutdown_still_flattens_when_explicitly_requested`,
`::test_shutdown_cancels_broker_stop_through_the_stop_api`,
`::test_kill_switch_preserves_armed_positions`,
`::test_apply_trailing_returns_none_when_kill_switch_on`,
`test_live_kill_switch.py` (миграция, гейт, fail-safe, API),
`test_live_alerting.py::test_graceful_shutdown_persists_the_final_snapshot`.


### 46.12 Сквозная зачистка сиротских стоп-ордеров (задача #199)

**Зачем.** Drill #193 увидел у брокера четыре ACTIVE-стопа при одной открытой позиции: три идентификатора не упоминались ни одной строкой `live_positions` (`drill-orphan-stops.txt`). OCO-проход #175 знает только ноги закрытий, выполненных *этим* процессом, поэтому стоп, осиротевший из-за падения между `PostStopOrder` и записью в БД, из-за неудачного закрытия, из-за amend, не подтвердившего свою отмену, или из-за ручного вмешательства, остаётся у брокера голым SELL-ордером: он сработает на ближайшей просадке и продаст акции, которых на счёте нет.

**Что исполняется.** `_sweep_orphan_stops()` — последний проход `_finish_monitor_cycle()`, то есть он выполняется и на раннем возврате при пустой книге позиций: это ровно тот момент, когда любой оставшийся на счёте стоп уже ничей. Период ограничен `orphan_stop_sweep_interval_seconds` (по умолчанию 300, независимо от `check_interval_seconds`: это сверка, а не проход каждого цикла), данные — `GetStopOrders(active)` плюс, если цикл портфель не читал, `GetPositions`: без подтверждения «брокер ничего не держит» зачистка не работает. Нечитаемая книга стопов или портфель пропускают проход (`orphan_stop_sweep_skipped reason=...`) и сохраняют кандидатов предыдущего.

**Что можно снять.** Только стоп, прошедший всю fail-closed цепочку `_orphan_stop_skip_reason()`: `not_active`, `not_a_sell_stop`, `outside_universe` (тикер / FIGI / instrument_uid настроенного live-универсума), `owned_by_oco_or_amend` (`_oco_checks` плюс обе стороны `_pending_stop_cancels`), `position_row_exists` (любая строка `pending` / `open` того же инструмента — исполнитель, возможно, прямо сейчас вооружает для неё стоп), `broker_holding_exists` (брокер всё ещё держит инструмент, значит снятие стопа — человеческое решение), `inside_grace_window` (`orphan_stop_grace_seconds`, по умолчанию 900, отсчёт от момента, когда стоп вооружил *этот* процесс). Каждый сохранённый стоп логируется `orphan_stop_kept stop_order_id=... reason=...` на DEBUG; пустой универсум пропускает проход вместо того, чтобы считать все стопы своими.

**Что даёт право действовать.** Один и тот же сирота должен быть увиден `orphan_stop_confirmations` (по умолчанию 2) проходами подряд — таблица кандидатов каждый проход заменяется целиком, поэтому исчезнувший между проходами стоп начинает счёт заново и «мерцающий» никогда не накапливает подтверждений. Подтверждённых сирот больше, чем `orphan_stop_max_cancels` (по умолчанию 3) — срабатывает fail-closed ветка: не снимается ничего вообще, растёт `orphan_sweep_fail_closed_total`, в лог уходит CRITICAL `orphan_stop_sweep_fail_closed orphans=N max_cancels=M`, а в Telegram — алерт с просьбой разобрать вручную, прореженный `orphan_stop_alert_interval_seconds` (по умолчанию 3600) поверх глобального debounce. `orphan_stop_max_cancels=0` — режим «только наблюдение». Отклонённая брокером отмена не засчитывается: кандидат остаётся и повторяется (`orphan_stop_cancel_failed`). Любое неожиданное исключение перехватывается, считается fail-closed и логируется `orphan_stop_sweep_failed` — страховочная сетка не имеет права ломать мониторинговый цикл.

**Вид для оператора.** `/api/live-trading/metrics` → `protection`: `orphan_stop_sweep_enabled`, `orphan_stop_sweep_runs_total`, `orphan_stop_candidates` (ждут следующего подтверждения), `orphan_stops_cancelled_total`, `orphan_sweep_fail_closed_total`. Ненулевой `fail_closed_total` игнорировать нельзя. Каждая снятая пачка дополнительно отправляет critical-алерт `Сняты бесхозные стоп-ордера` с перечислением идентификаторов, потому что позиции за ними остаются без защиты. Все ручки читаются на каждом проходе, поэтому изменение `LIVE_TRADING` в рантайме действует со следующей зачистки без рестарта; `_validate_config()` на старте отвергает не-булев `orphan_stop_sweep_enabled`, `orphan_stop_confirmations` меньше 1 и отрицательный `orphan_stop_max_cancels`.

**Остановка (RC2).** `shutdown()` передаёт `force=True` в `_cancel_pending_stops()`: запись появляется только после того, как `PostStopOrder` вернул новый идентификатор, значит стоп-замена уже у брокера, и снятие вытесненного на выходе не может оставить позицию без защиты — а вот оставление оставляет голый sell-стоп, который больше никому из живых процессов не принадлежит.

**Тесты.** `cd backend && python -m pytest -q tests/test_live_executor.py -k "orphan or shutdown_drops"` (подтверждения и их сброс, интервал, отключение, каждая причина пропуска, пустой универсум, владение строками БД, претензии OCO/amend, брокерские позиции, grace-окно, сбои книги стопов и портфеля, отклонённая отмена, потолок / fail-closed, отправка и прореживание алертов, перечитывание ручек в рантайме, цикл с пустой книгой, принудительная отмена при остановке, дефолты / клампинг / отказ старта на невалидных ручках) плюс `tests/test_live_alerting.py` на контракт метрик.

### 46.14 Canary-контур: один лот SBER за двумя паузами оператора (задача #194)

**Зачем.** Первая сделка на реальные деньги обязана быть минимально возможным доказательством всей цепочки, а не стратегической сессией: ОДИН тикер, ОДИН лот, ОДНА открытая позиция и человек, который подтверждает единственный ордер дважды — до того, как что-либо уйдёт брокеру, и после того, как филл защищён. Всё остальное остаётся боевым контуром: те же гейты `LIVE_RISK` (решение D3 — canary не выдумывает собственные риск-лимиты), та же брокерская защита #175, та же сквозная зачистка сирот #199, тот же kill switch #178 и та же остановка потока без flatten #193.

**Политика живёт в одном месте.** `trading_config.CANARY` (`enabled=False`, `ticker='SBER'`, `max_lots=1`, `max_open_positions=1`, `allow_outside_entry_window=False`), диапазоны `CANARY_BOUNDS` (`max_lots` и `max_open_positions` в пределах [1, 100]) и карта env `CANARY_ENV` — четыре ручки (`CANARY_ENABLED`, `CANARY_TICKER`, `CANARY_MAX_LOTS`, `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW`), потому что `max_open_positions` остаётся конфигурационным: canary с двумя позициями — уже не canary. `get_canary_config()` возвращает изолированную копию и сливает дефолты → env → словарь вызывающей стороны; **пустая** ручка считается незаданной и сохраняет дефолт, а **испорченная** бросает `ValueError` на старте (`_env_strict_bool` для флага, `normalize_canary_ticker` для имени, `_env_bounded_int` для потолка), поэтому контур никогда не стартует на догадке. `validate_canary_values()` применяет те же правила к словарю от вызывающей стороны (тесты и drill): `canary={'enabled': True, 'max_lots': 999}` отклоняется, частично заполненный словарь — нет.

**Что исполнитель делает иначе** — всё за флагом `canary_enabled`, обычный прогон остаётся байт-в-байт как в #199:

* конструктор логирует `CANARY MODE ON: ticker=... max_lots=... max_open_positions=... confirmations=2` и зажимает `max_open_positions = min(config, canary)`, поэтому env `MAX_OPEN_POSITIONS=5` не может расширить canary, а canary не может расширить обычный контур;
* `initialize()` сужает универсум через `_apply_canary_universe()`: canary-тикер и только если `trading_universe` уже помечен `live_trading_enabled`. Опечатка в `CANARY_TICKER` оставляет универсум ПУСТЫМ (`Canary universe is EMPTY: ticker=... is not live-enabled` на ERROR), и цикл не выставляет ордеров вообще — fail-closed, а не «торгуем дефолт»;
* `process_signal()` повторяет этот гейт сразу после kill switch и до сессионного окна, стакана, сайзинга и любого брокерского вызова: чужое имя возвращает причину `canary_universe`, поэтому прямой вызов (drill, ручной реплей) не может протащить второй тикер;
* календарь сессии #137 может быть отключён **только для canary** (решение PO от 2026-10-03: `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true`, в поставке `false`). Тогда `_entry_window_open()` отвечает «окно открыто» вне [10:00, 19:00) MSK по будням, поэтому canary в выходной / внебиржевую сессию вообще способен войти. Ручка читается ровно в трёх местах — гейт входа в `process_signal()`, гейт `process_latest_bars()` в главном цикле и `wait_for_session_open()`, который сразу возвращается вместо сна до понедельника. Всё остальное не ослаблено: kill switch, гейт canary-универсума, риск-гейт, фильтры свежести стакана и дисбаланса, сайзинг, потолок в 1 лот и обе паузы оператора работают как прежде. Каждый пропущенный **сигнал** увеличивает `canary_window_bypass_total` (опросы цикла — нет, поэтому двухчасовой прогон не накручивает счётчик), первый обход логируется строкой `CANARY: entry window bypassed at <MSK> (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true) - the #137 calendar gate ...`, конструктор — `CANARY: entries are allowed OUTSIDE the MOEX entry window ...`, а алерты `live_start` / `live_entry` получают строку `Вход вне окна сессии: разрешён (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true)`. Обычный прогон ручку не читает: `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` без `CANARY_ENABLED=true` не меняет ничего;
* потолок применяется **после** сайзера и после гейта нотила #176 и только уменьшает: `Live canary cap: ticker=SBER size_lots=10 -> 1 reason=canary_cap sizer_reason=risk`. Публикуемая причина сайзинга становится `canary_cap`, поэтому отчёт разделяет «риск разрешал 10 лотов, canary срезал до 1» и «риск разрешал 1»;
* **пауза 1** (`_canary_confirm_entry()`, до `execute_order`): `[CANARY <контур>] Готов к покупке N лот(ов) TICKER по ~X руб., стоп=..., тейк=..., дисбаланс стакана=... Подтвердите (y/n, retry - перечитать стакан)`. `retry` перечитывает агрегат стакана и спрашивает снова, не более `CANARY_CONFIRM_MAX_RETRIES=3` раз, после чего вход сбрасывается. Всё, кроме явного yes (`y` / `yes` / `д` / `да`), — отказ: растёт `canary_rejections_total`, сигнал пропускается с причиной `canary_not_confirmed`, мутирующих брокерских вызовов **ноль**, уходит critical-алерт `Canary-вход отклонён оператором`. Бросающая исключение `confirm_fn` и EOF (закрытый stdin, пайп, `docker compose exec` без `-T`, detached-старт) отвечают «нет» — неподтверждённый запрос никогда не считается одобрением;
* **пауза 2** (`_canary_post_entry_gate()`, после филла и его защиты): `y` продолжает мониторинг позиции; `retry` перевыставляет ТОЛЬКО недостающую ногу через `_canary_rearm_protection()` — уже вооружённый брокерский стоп никогда не дублируется, потому что второй активный SELL-стоп перепродаст позицию на ближайшей просадке и станет следующим сиротой #199; `n` либо исчерпанный лимит повторов вызывают `_canary_abort()`.

**Abort — это не flatten.** `_canary_abort()` пишет CRITICAL `CANARY ABORT: position_id=... ticker=... lots=... status=... stop_armed=... take_placed=...`, увеличивает `canary_aborts_total`, отправляет critical-алерт `Canary остановлен оператором — позиция НЕ закрыта` (его последняя строка указывает на `handover.ru.md §46.11 — ручное закрытие`), взводит `shutdown_requested` через `request_shutdown()` и возвращает `executed=True, reason=canary_aborted`: ордер действительно случился, и отчёт не должен читаться как «открыт». Дополнительно устанавливается `_canary_abort_no_flatten`, который внутри `shutdown()` принудительно выключает `close_positions_on_shutdown` (`Canary abort: close_positions_on_shutdown forced OFF - the open position keeps its broker protection and waits for the runbook`) даже на деплое, закрывающем позиции: деньги уже на рынке с вооружённой брокерской защитой, и ликвидировать их из-за отклонённого промпта было бы единственным необратимым действием, которого оператор не просил. Позиция закрывается собственным стопом/тейком либо по runbook C §46.11.

**Вид для оператора.** `get_metrics()` публикует поля идентичности `canary_enabled` / `canary_ticker` / `canary_max_lots` — `None`, а не поддельный ноль, когда canary выключен, — плюс шесть счётчиков `canary_capped_total`, `canary_rejections_total`, `canary_confirmations_total`, `canary_confirm_retries_total`, `canary_aborts_total`, `canary_window_bypass_total` и флаг `canary_allow_outside_entry_window` (`None`, а не `False`, когда canary выключен). `GET /api/live-trading/metrics` собирает их в собственную секцию `canary` (`enabled`, `ticker`, `max_lots`, `capped_total`, `rejections_total`, `confirmations_total`, `confirm_retries_total`, `aborts_total`, `allow_outside_entry_window`, `window_bypass_total`), а не прячет в `risk`: оператор обязан с одного взгляда видеть «этот цикл имеет право купить только 1 лот SBER». Семантика счётчиков: `confirmations_total` считает промпты, на которые человек **ответил** (отказ — тоже ответ), `rejections_total` — отклонённые входы, `capped_total` — срезанные сайзы, поэтому один отклонённый запрос на 10 лотов двигает сразу три. Алерты `live_start` / `live_entry` получают блок `_canary_lines()` (`Canary: включён`, `Canary-тикер`, `Canary-лимит`, `Подтверждения: 2 паузы: перед ордером и после защиты`, `Сайзер`, `Стоп у брокера`, `Тейк у брокера`, а при включённом обходе календаря — ещё `Вход вне окна сессии: разрешён (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true)`) только внутри canary; обычный прогон не добавляет ничего и сохраняет тело #177.

**Запуск canary.** Подтверждения читаются из stdin, поэтому прогон — фронтовый, с оператором у терминала, внутри окна входа (10:00–19:00 MSK). Прогону в выходной / внебиржевую сессию нужна ещё одна ручка — `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` (решение PO от 2026-10-03); без неё каждый сигнал пропускается с причиной `outside_entry_window` и прогон не выставляет ничего:

```bash
# 1. preflight должен совпадать с контуром, которым вы собираетесь торговать
docker compose exec -T backend python -m app.analytics.live_executor_preflight
#    на реальном контуре: PREFLIGHT_EXPECT_CONTOUR=real (см. §46.3 шаг 3)

# 2. фронтовый старт; DURATION_MINUTES — первый argv для `python -m`
docker compose exec -T -e CANARY_ENABLED=true -e CANARY_TICKER=SBER \
  -e CANARY_MAX_LOTS=1 backend python -u -m app.analytics.live_executor 120

# 2б. canary в выходной / внебиржевую сессию: тот же прогон плюс обход календаря #137
docker compose exec -T -e CANARY_ENABLED=true -e CANARY_TICKER=SBER \
  -e CANARY_MAX_LOTS=1 -e CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true \
  backend python -u -m app.analytics.live_executor 120
```

Первая строка лога для проверки — `CANARY MODE ON: ticker=SBER max_lots=1 max_open_positions=1 confirmations=2`, затем `Canary universe narrowed: ... -> SBER (max_lots=1)`. Без них процесс является обычным циклом и торгует весь универсум. Прогон с обходом дополнительно логирует `CANARY: entries are allowed OUTSIDE the MOEX entry window (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true) ...` в конструкторе и `CANARY: entry window bypassed at <MSK> ...` на первом пропущенном сигнале; свежие стаканы при этом всё равно обязательны (`run_data_refresher` / `run_online_data`), потому что несвежий отклоняется как `stale_or_missing_orderbook` точно так же, как в будний день.

**Drill (задача #194).** `194-canary-drill.py` лежит вместе с артефактами в `reports/190-production-trading-infrastructure/194-issue-194-canary-1-lot-sber/` (в образ не входит, в торговом пути не появляется). Десять фаз: `environment` → `connections` → `policy_failfast` → `window_bypass` → `universe` → `cap` → `confirmations` → `abort_without_flatten` → `metrics_alerts` → `full_chain` (opt-in). Drill песочный по построению: если `create_execution_client()` разрешается в реальный контур, он останавливается с `DRILL_BLOCKED` (exit 3), ничего не тронув. Вне `--full-chain` четыре мутирующих метода брокера (`execute_order`, `cancel_order`, `post_stop_order`, `cancel_stop_order`) затеняются **на экземпляре** счётными заглушками, установка которых проверяется по маркеру (никогда пробным вызовом на реальном клиенте), ответ оператора жёстко зашит как `n`, а в базу не пишется ничего. `--full-chain` дополнительно требует `--i-understand-sandbox-order`, отказывается стартовать при включённом `live_kill_switch` или при любой строке `pending`/`open`, выставляет ровно один ордер на `CANARY_MAX_LOTS` лотов через боевой `process_signal`, проверяет защиту у брокера и сам позицию не закрывает (`--cleanup` закрывает *завершённый* вход через боевой путь shutdown, никогда после abort). Коды возврата: `0` `DRILL_OK` / `1` `DRILL_FAIL` / `3` `DRILL_BLOCKED`.

```bash
cd reports/190-production-trading-infrastructure/194-issue-194-canary-1-lot-sber
docker compose cp 194-canary-drill.py backend:/tmp/194-canary-drill.py

# герметично: только фейки, без БД / API / брокера — безопасно где угодно
docker compose exec -T backend python /tmp/194-canary-drill.py --self-test

# песочный drill: живые API и база, read-only вызовы брокера, без ордера
docker compose exec -T backend sh -c \
  'python /tmp/194-canary-drill.py --json /tmp/drill-194.json > /tmp/drill.txt 2>&1; echo exit=$?'
docker compose cp backend:/tmp/drill.txt      <папка-issue>/drill-sandbox.txt
docker compose cp backend:/tmp/drill-194.json <папка-issue>/drill-sandbox.json
```

Статус 2026-10-03 (`--self-test`, `194-self-test.json`): `DRILL_OK`, exit `0`, 9 фаз / 238 проверок / **0 провалов**, `full_chain` пропущен по построению, все четыре заглушки `blocked`, `mutating_attempts` пуст, контур `fake`. Идентификаторы позиций читаются из ответа исполнителя, а не хардкодятся, поэтому логовые проверки (`Canary retry: stop re-arm position_id=... -> armed`, `CANARY ABORT: position_id=...`) выполняются на любой базе; кейс отказа проверяет `capped_total=1`, `rejections_total=1` и `confirmations_total=1` вместе — это ровно семантика счётчиков выше.

Статус 2026-10-03 (песочный drill внутри пересобранного контейнера, `drill-sandbox.txt` / `drill-sandbox.json`): `DRILL_OK`, exit `0`, 9 фаз / **247 проверок / 0 провалов**, `contour=sandbox`, `full_chain` пропущен (не запрошен), все четыре заглушки `blocked`, `mutating_attempts` пуст, строка `live_kill_switch` = `false`, развёрнутый универсум из 12 тикеров сужен до `SBER`. Фаза `metrics_alerts` прочитала **живой** `GET /api/live-trading/metrics` и подтвердила развёрнутую секцию: `{'enabled': False, 'ticker': None, 'max_lots': None, 'capped_total': 0, 'rejections_total': 0, 'confirmations_total': 0, 'confirm_retries_total': 0, 'aborts_total': 0}` — форма, которую публикует обычный цикл, без поддельной canary-идентичности.

Статус 2026-10-03 (после изменения с обходом календаря, в пересобранном образе; `drill-selftest-image-bypass.txt/.json`, `drill-sandbox-image-bypass.txt/.json`): `--self-test` → `DRILL_OK`, exit `0`, **10 фаз / 268 проверок / 0 провалов** — из них новая фаза `window_bypass` даёт 23; песочный drill против живого API → `DRILL_OK`, exit `0`, **10 фаз / 279 проверок / 0 провалов**, `contour=sandbox`, все четыре заглушки `blocked`, `mutating_attempts` пуст, а развёрнутая секция `canary` теперь читается как `{'enabled': False, 'ticker': None, 'max_lots': None, 'capped_total': 0, 'rejections_total': 0, 'confirmations_total': 0, 'confirm_retries_total': 0, 'aborts_total': 0, 'allow_outside_entry_window': False, 'window_bypass_total': 0}`.

**Известные ограничения.**

- Подтверждения только через stdin: detached-старт, `nohup`, `docker compose exec` без `-T` или любой пайп отвечают EOF, а EOF — это «нет». Поэтому canary не может работать фоновым сервисом: это смысл режима, а не дефект.
- `CANARY_CONFIRM_MAX_RETRIES=3` — константа модуля, а не ручка. После трёх ответов `retry` пауза сдаётся: первая сбрасывает вход, вторая останавливает поток. Оператору, который не вооружил защиту за три попытки, нужен runbook C §46.11, а не новый промпт.
- Потолок задан в **лотах**, а не в нотиле: `MAX_POSITION_SIZE` и остальной `LIVE_RISK` применяются без изменений (решение D3), поэтому canary на дорогом имени по-прежнему ограничен обычными риск-гейтами, а сам потолок никогда не расширяет сайз, который сайзер уже срезал.
- Canary — режим процесса исполнителя, а не персистентное состояние: после рестарта runbook обязан снова выставить `CANARY_ENABLED`, а обычный цикл продолжает публиковать `canary_enabled=false` с `ticker` / `max_lots` = `null`.
- Canary в выходной / внебиржевую сессию (`CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true`) по-прежнему требует свежих данных и брокера, принимающего эту сессию: обход снимает только **календарный** гейт, поэтому несвежий стакан отклоняется как `stale_or_missing_orderbook` (старше `ORDERBOOK_IMBALANCE.max_age_minutes`) ровно так же, как в будний день, а тонкая ликвидность выходного дня означает больший слиппедж на одном лоте. Перед прогоном поднимите `run_data_refresher` / `run_online_data` и перечитайте preflight (`fresh_orderbooks`, `paper_processes`).
- `full_chain` — единственный путь в drill, способный выставить ордер: ему нужны два явных флага и чистый счёт. Остальные девять фаз не могут мутировать ни брокер, ни базу данных.

**Тесты.** `cd backend && python -m pytest -q tests/test_live_executor.py -k "canary or confirm or abort or cap"` (поставочные дефолты и изолированная копия, env-переопределения и каждая fail-fast ветка, «пустое значит не задано», нормализация тикера, приоритет словаря вызывающей стороны над env, потолок в 1 лот и «только уменьшает», `min(canary, risk)` для открытых позиций, сужение универсума и его fail-closed ветка, отказ `canary_universe` на прямом вызове, обе паузы включая бросающую `confirm_fn` и закрытый stdin, лимит повторов, перевыставление только недостающей ноги, abort без flatten и его принудительно выключенный флаг shutdown, счётчики в снимке, строки алертов и обход календаря #137 по решению PO от 2026-10-03 — дефолт OFF, строгий булев разбор env, субботний вход по-прежнему в 1 лот и за обеими паузами, гейт главного цикла, `wait_for_session_open`, изоляция обычного контура, `canary_window_bypass_total` и строка алерта `Вход вне окна сессии`) плюс `tests/test_live_alerting.py` на поля снимка `canary_*` и секцию `canary` в `GET /api/live-trading/metrics`.
