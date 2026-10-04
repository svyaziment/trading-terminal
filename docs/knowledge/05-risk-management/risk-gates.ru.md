# Live equity и риск-гейты (задача #176, эпик #172, блок D)

> **Source:** project-context.ru.md sections 22 + handover.ru.md sections 44
> **Last refreshed:** 2026-10-04, task-346

## 22. Live equity и риск-гейты (задача #176, эпик #172, блок D)

Завершено 2026-09-27. Live-контур теперь ведёт собственный учёт эквити и применяет
риск-лимиты до каждого входа. Paper-контур (`paper_equity`, `write_equity`,
`config_manager.RiskConfig`) не затронут.

**Формула эквити**: `equity = cash + market_value`, где `cash` — свободный RUB-остаток
из `GetSandboxPositions`, а `market_value` оценивает каждую позицию из
`GetSandboxPortfolio` по `current_price` (с откатом на `average_price`; позиция без
цены исключается и логируется, а не выдумывается). Realized PnL **не** добавляется: он
уже внутри брокерского кэша, поэтому повторное сложение задвоило бы его и занизило
drawdown, по которому считается гейт. `realized_pnl_rub` (сумма `live_positions.pnl_rub`
по закрытым строкам) и `unrealized_pnl_rub` (`market_value - cost_basis`) пишутся
отдельными наблюдательными колонками.

**Маркировка цен (задача #191)**: цена брокера пригодна, только если она парсится в
**конечное число строго больше нуля** (`LiveExecutor._mark_price`). `current_price` равный
`0`, отрицательный или неконечный откатывается на `average_price`, а тикер попадает в
`stale_priced_holding_tickers`; если и `average_price` непригодна, позиция исключается из
`market_value`, а её тикер — в `unpriced_holding_tickers` (по одному warning'у
`live equity: no usable price for holding ...` на каждую такую позицию в снимке). Обе
деградации считаются в `holdings_unpriced_total` / `holdings_stale_priced_total`
(накопительно за время жизни процесса), уходят в Telegram (`unpriced_holding:<TICKER>` и
`holding_marked_at_average:<TICKER>` — оба critical и с dedupe, плюс
`unpriced_holding_resolved:<TICKER>`, когда брокер снова дал цену), передаются в детали
алерта `risk_breach` и отдаются через `get_metrics()` рядом с `equity_last_cash_rub` /
`equity_last_market_value_rub`. Списки тикеров описывают только последний снимок и
очищаются на следующем полностью читаемом, поэтому растущий счётчик означает, что
брокерская лента всё ещё неисправна. Именно это не даёт свежеоткрытой позиции быть
оценённой в ноль и породить просадку, которой у счёта никогда не было.

**Базис дневного убытка**: `session_key` — календарный день МСК снимка.
`peak_equity_rub` — пик **в пределах этого `session_key`**, что и делает
`max_daily_loss_pct` дневным лимитом; пожизненный пик превратил бы его в лимит
просадки за всю историю и мог бы заблокировать входы навсегда.
`peak_equity_all_time_rub` хранится отдельной колонкой для мониторинга.
`drawdown_pct = (peak - equity) / peak * 100`.

**Гейты** (все в `LiveExecutor.process_signal`, в таком порядке):
1. `risk_breach` — гейт дневной просадки. Считается из снимка в памяти, поэтому не
   требует ни брокерского вызова, ни запроса к БД, и выполняется до стакана и сайзинга.
   При breach блокируются **только новые входы**: существующие позиции сохраняют
   брокерские стопы, а `monitor_positions()` продолжает довыставлять отсутствующие.
   **Принудительного flatten нет** (решение Product Owner от 2026-09-18).
2. `max_open_positions` — существовавший гейт, не изменён; `LIVE_TRADING` остаётся его
   единственным источником истины (#176 добавил лишь env-override `MAX_OPEN_POSITIONS`).
3. `position_size_limit` — абсолютный лимит нотила
   `size_lots * lot_size * entry_price > max_position_size`, применяется после
   `calculate_position_size` и до `execute_order`. Считается по исполняемому ордеру, а не
   по бюджету сайзера до округления, поэтому ветка `min_lot` (принудительно поднимающая
   `size_lots` до 1) не может провести позицию больше лимита.

**Восстановление после breach**: автоматически на следующий торговый день МСК (новый
`session_key` начинает новый пик) либо вручную установкой
`trading.app_settings.live_risk_breach_reset` в `true`. Флаг самопоглощающийся —
исполнитель пишет обратно `false`, — поэтому зависший `true` не может снять более
поздний breach. `initialize()` повторно взводит breach, уже истинный для текущего
`session_key`, так что рестарт процесса нельзя использовать для обхода гейта.
Взведенный breach остаётся `true` до конца дня даже при восстановлении эквити, поэтому
сохранённый флаг и гейт не могут разойтись внутри одной сессии.

**Конфигурация** (`trading_config.py`, секция `LIVE_RISK` + `get_live_risk_config()`):
- `max_daily_loss_pct`: по умолчанию `2.0`, env `MAX_DAILY_LOSS_PCT`, диапазон `(0, 100]`.
- `max_position_size`: по умолчанию `100000` RUB, env `MAX_POSITION_SIZE`, диапазон `(0, 1e12]`.
- `equity_snapshot_enabled`: по умолчанию `true`, env `LIVE_EQUITY_SNAPSHOT`. Выключение
  отключает и снимок, и гейт просадки — это dry-run-переключатель всего риск-контура.
- `max_open_positions`: по умолчанию `5` в `LIVE_TRADING`, env `MAX_OPEN_POSITIONS`, диапазон `[1, 100]`.

Непарсимое или внедиапазонное значение env бросает `ValueError` в момент чтения, поэтому
опечатка в `.env` падает сразу, а не молча отключает риск-гейт.
`LiveExecutor._validate_config` повторно проверяет те же границы, так что in-memory
override не может протащить значение, которое `.env` отверг бы. Эти лимиты намеренно
**не** живут в `config_manager.RiskConfig`: тот объект — paper-политика риска из
`config/settings.yaml`, потребляемая `paper_trader.write_equity`.

**База данных**: миграция `20260927_001_live_equity.py` (`down_revision = 20260916_001`)
создаёт `trading.live_equity` с двумя индексами (`timestamp DESC` и
`(session_key, timestamp DESC)`) и засевает `live_risk_breach_reset` в
`trading.app_settings`. `live_schema.ensure_live_equity_schema()` — идемпотентная runtime-форма
того же DDL, вызываемая из `initialize()`, поэтому автономный запуск исполнителя сходится
на немигрированной БД. `timestamp` — `TIMESTAMP WITHOUT TIME ZONE` с naive MSK, как в
`paper_equity.timestamp` и `live_positions.signal_ts`.

**Отказоустойчивость**: `_write_live_equity()` вызывается один раз за цикл из `run()`,
**до** `monitor_positions()` и `process_latest_bars()`, чтобы все гейты читали свежую
просадку. Метод целиком обёрнут в `try/except`: сбой снимка увеличивает
`equity_snapshot_errors_total`, пишет warning и никогда не пробрасывается в цикл и не
трогает `_consecutive_errors`. Его два брокерских вызова используют новый приоритет
rate-limit `equity` с `blocking=False` и тем же резервом токена, что `entry`, поэтому
снимок откладывается (счётчик `equity_snapshot_skipped_total`), а не отнимает токен,
нужный постановке или amend стопа. До первого снимка гейт просадки **fail-open** и один
раз пишет warning: блокировать все входы из-за заминки rate-limit хуже, чем отторговать
один цикл без свежего значения.

**Алерты**: переход в breach один раз пишет `logger.critical` (не каждый цикл),
**немедленно сбрасывает снимок метрик** (#191 — алерт не должен оставаться невидимым до
60 с, пока гейт входа уже отклоняет сигналы) и отдаёт счётчики через `get_metrics()`:
`risk_breach_active`, `risk_breach_total`,
`risk_breach_resets_total`, `risk_gate_rejections_total`, `position_size_rejections_total`,
`equity_snapshots_total`, `equity_snapshot_errors_total`, `equity_snapshot_skipped_total`,
`holdings_unpriced_total`, `holdings_stale_priced_total`,
`unpriced_holding_tickers`, `stale_priced_holding_tickers`,
`last_equity_rub`, `last_drawdown_pct`, `last_peak_equity_rub`, `last_equity_session_key`,
`equity_last_cash_rub`, `equity_last_market_value_rub`
и действующие лимиты. Счётчики позиций `*_total` накопительные за время жизни процесса,
списки тикеров описывают только последний снимок. Telegram-сообщение `risk_breach` несёт
тот же контекст измерения (кэш, стоимость позиций, тикеры без цены и тикеры, отмеченные по
средней), а payload отклонения гейта просадки дополнен полем `unpriced_holdings` — именно
это позволяет отличить реальную просадку от сбоя маркировки без чтения лога контейнера.
`GET /api/live-trading/metrics` отдаёт то же разложение как `risk.last_cash_rub` /
`risk.last_market_value_rub` рядом с `risk.unpriced_holding_tickers` /
`risk.stale_priced_holding_tickers`. Telegram-доставка `risk_breach` реализована блоком E
(#177) — см. §23.

**Тестирование**: `backend/tests/test_live_equity_risk_gates.py` (83 тестов) покрывает
контракт схемы и цепочку миграций, дефолты / env-override / валидацию диапазонов, формулу
эквити и отсутствие двойного счёта, маркировку цен и учёт позиций без цены / с устаревшей
ценой, последовательность 28.09 с фантомным breach, дневной и глобальный пик, взведение и
снятие breach (авто, ручное, рестарт), оба гейта входа, отложение по rate-limit, изоляцию
сбоев, `get_metrics()` и три эндпоинта.

## 44. Эксплуатация риск-гейтов live equity (задача #176)

### Зачем

До #176 live-контур вообще не вёл учёт эквити: таблицы `trading.live_equity` не
существовало, риск-лимиты из `config/settings.yaml` читал только paper-трейдер, а
поведение при просадке счёта нигде не определялось. #176 добавляет снимок, гейты и
порядок восстановления. Детали архитектуры — `project-context.ru.md` §22.

### Что изменилось

- `backend/alembic/versions/20260927_001_live_equity.py` — создаёт `trading.live_equity`
  (16 колонок, 2 индекса) и засевает `live_risk_breach_reset` в `trading.app_settings`.
- `backend/app/analytics/trading_config.py` — новая секция `LIVE_RISK`,
  `get_live_risk_config()`, `get_live_risk_bounds()` и env-переопределения
  `MAX_DAILY_LOSS_PCT` / `MAX_POSITION_SIZE` / `MAX_OPEN_POSITIONS` / `LIVE_EQUITY_SNAPSHOT`.
  `get_live_trading_config()` теперь разрешает override `MAX_OPEN_POSITIONS`.
- `backend/app/analytics/live_schema.py` — `ensure_live_equity_schema()` /
  `ensure_live_runtime_schema()`, `REQUIRED_LIVE_EQUITY_COLUMNS`,
  `LIVE_EQUITY_SCHEMA_STATEMENTS`, `RISK_BREACH_RESET_KEY`. Хранится в **отдельном**
  кортеже операторов: `test_live_schema.py` проверяет, что в `LIVE_SCHEMA_STATEMENTS`
  нет `DROP TABLE` / `DROP COLUMN`, а downgrade эквити именно их и использует.
- `backend/app/analytics/live_executor.py` — `_write_live_equity()`, `_compute_live_equity()`,
  `_daily_peak_equity()`, `_all_time_peak_equity()`, `_realized_pnl_rub()`,
  `_risk_gate()`, `_activate_risk_breach()`, `_clear_risk_breach()`,
  `_restore_risk_breach_state()`, `_refresh_risk_breach_reset()`, `_account_id()`;
  приоритет rate-limit `equity`; два гейта в `process_signal()`; вызов снимка в `run()`;
  риск-поля в `get_metrics()`.
- `backend/app/api/live_trading_jobs.py` — `/api/live-trading/equity/current`,
  `/latest` (алиас) и `/history`.
- `.env.example` — четыре новые переменные.

### Что обязан знать эксплуатант

- Гейт **дневной**, а не пожизненный. `peak_equity_rub` сбрасывается вместе с
  `session_key` (календарный день МСК), поэтому breach снимается сам на следующий
  торговый день.
- Breach блокирует **только новые входы**. Открытые позиции сохраняют брокерские стопы,
  а цикл мониторинга продолжает довыставлять отсутствующие. Принудительного flatten нет.
- Взведенный breach остаётся до конца дня МСК даже при полном восстановлении эквити.
- Рестарт исполнителя **не** снимает breach: `initialize()` повторно взводит его из
  последней строки `live_equity` текущего `session_key`.
- До первого снимка в процессе гейт просадки **fail-open** (один раз логируется warning).
  Отсутствие или отложение снимка входы не блокирует.
- `LIVE_EQUITY_SNAPSHOT=off` отключает и снимок, **и** гейт просадки. Лимит нотила и
  `max_open_positions` продолжают работать — они от эквити не зависят.
- Сбой снимка торговлю не останавливает: растёт `equity_snapshot_errors_total` и пишется
  warning. Следить нужно за этим счётчиком, а не за циклом.


### Команды

```bash
# Применить миграцию (в образ backend alembic/ не входит — см. ограничения)
cd backend && python -m alembic upgrade head
python -m alembic current      # ожидается 20260927_001 (head)
python -m alembic downgrade -1 # откат; удаляет trading.live_equity
python -m alembic upgrade head

# Проверить таблицу и засеянный переключатель сброса
psql -c "SELECT count(*) FROM trading.live_equity;"
psql -c "SELECT key, value FROM trading.app_settings ORDER BY key;"

# Последний снимок и действующие лимиты
curl -s http://localhost:8000/api/live-trading/equity/current | python -m json.tool
curl -s "http://localhost:8000/api/live-trading/equity/history?limit=50"

# Ручное снятие активного breach (флаг самопоглощающийся: исполнитель пишет false обратно)
psql -c "UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() \
         WHERE key='live_risk_breach_reset';"

# Переопределить лимиты на один запуск без правки кода
MAX_DAILY_LOSS_PCT=1.5 MAX_POSITION_SIZE=50000 MAX_OPEN_POSITIONS=3 \
  python -m app.analytics.live_executor

# Тесты (83)
cd backend && python -m pytest tests/test_live_equity_risk_gates.py -q
```

### Триаж фантомной просадки (задача #191)

28.09.2026 исполнитель взвёл `risk_breach`, которого у счёта не было: свежеоткрытая
позиция вернулась из `GetSandboxPortfolio` с `current_price = 0`, `market_value` молча
потерял эту позицию, эквити просел и сработал дневной гейт просадки. #191 делает
маркировку явной вместо молчаливой.

- Цена пригодна, только если парсится в **конечное строго положительное** число
  (`LiveExecutor._mark_price`). Непригодный `current_price` (`0` / отрицательный / `nan`)
  откатывается на `average_price`, а тикер попадает в `stale_priced_holding_tickers`.
  Если пригодной цены нет вовсе, позиция остаётся вне `market_value`, а её тикер — в
  `unpriced_holding_tickers`.
- Оба списка публикуются по каждому снимку: в Telegram (`unpriced_holding:<TICKER>`
  critical, `unpriced_holding_resolved:<TICKER>` когда брокер снова дал цену,
  `holding_marked_at_average:<TICKER>` — оба `critical` + `dedupe=True`; сообщение
  о возврате цены без debounce, исполнитель отправляет его ровно один раз на тикер),
  в `get_metrics()`
  (`unpriced_holding_tickers`, `stale_priced_holding_tickers`, накопительные
  `holdings_unpriced_total` / `holdings_stale_priced_total`, `equity_last_cash_rub`,
  `equity_last_market_value_rub`) и в блоке `risk` ответа
  `GET /api/live-trading/metrics` (`unpriced_holding_tickers`,
  `stale_priced_holding_tickers`, `holdings_unpriced_total`,
  `holdings_stale_priced_total`, `last_cash_rub`, `last_market_value_rub`). Списки
  тикеров описывают только последний снимок и очищаются на следующем полностью читаемом;
  счётчики `*_total` накопительные за время жизни процесса, поэтому их рост означает, что
  лента всё ещё неисправна.
- Алерт `risk_breach` несёт то же измерение рядом с процентом (`Кэш`,
  `Стоимость позиций`, `Без цены`, `По средней цене`), а payload отклонения гейта
  просадки дополнен полем `unpriced_holdings` — заблокированный вход сообщает, было ли
  просадка измерена на портфеле, который брокер способен оценить.
- Взведение breach теперь **немедленно сбрасывает снимок метрик**
  (`_flush_metrics(force=True)`), поэтому `risk_breach_active` в `/metrics` больше не
  отстаёт на `LIVE_ALERTING.metrics_flush_seconds` (300 по умолчанию) от гейта, который
  уже отклоняет входы.

**Порядок триажа при срабатывании `risk_breach`:**

1. Прочитать алерт (или `/api/live-trading/metrics`). Непустой `unpriced_holding_tickers`
   или `stale_priced_holding_tickers` означает, что просадка измерена на неполном
   портфеле — сначала разбираемся с этим как с инцидентом брокерской ленты.
2. Сверить разложение: `last_cash_rub + last_market_value_rub` должно равняться
   `last_equity_rub`. Обвал `market_value` при неизменном `cash` — это маркировка, а не
   убытки.
3. Подтвердить историей и логом:
   `curl -s "http://localhost:8000/api/live-trading/equity/history?limit=50"` и
   warning'и `live equity: N holding(s) ...` в `docker compose logs backend`.
4. Если цифры настоящие — оставляем breach взведённым до конца дня МСК. Если это артефакт
   ленты — дожидаемся чистого снимка (счётчики сбросятся сами) и только потом снимаем
   latch через `live_risk_breach_reset`.

### Известные ограничения

- **`alembic` отсутствует в образе backend.** `backend/Dockerfile` копирует только `app/`
  и `tests/`, поэтому `docker compose exec backend alembic ...` падает с
  `No 'script_location' key found`. Миграции нужно запускать с хоста либо предварительно
  скопировать `backend/alembic.ini` и `backend/alembic` в `/app` через `docker compose cp`.
  Шаг деплоя `alembic upgrade head` — блок F (#178).
- **`app/core/config.py:get_app_database_url()` читает только `POSTGRES_PASSWORD`**, тогда
  как `docker-compose.yml` передаёт `PSTGRS_PWD`. Внутри контейнера alembic пытается
  подключиться с паролем по умолчанию `app` и падает; ошибка выглядит как обманчивый
  `UnicodeDecodeError` из psycopg2, потому что русское сообщение сервера не в UTF-8.
  Обход, использованный при проверке:
  `docker compose exec -T backend sh -c 'POSTGRES_PASSWORD="$PSTGRS_PWD" alembic upgrade head'`.
  `config_manager.load_settings()` уже принимает оба имени — выравнивание
  `app/core/config.py` с ним относится к #178.
- `strategy_name` равен `NULL` в снимках, записанных вне полного прогона `initialize()`
  (именно там исполнитель узнаёт стратегию).
- Гейт просадки читает снимок из памяти текущего процесса. Если исполнитель не работает,
  новые снимки не пишутся и гейт не может ужесточиться — по устаревшим данным он
  ретроспективно не блокирует, кроме описанного выше восстановления при старте.
- Telegram-алерт о `risk_breach` реализован блоком E (#177): `LiveExecutor._notify("risk_breach", ..., critical=True)` уходит поверх того же `logger.critical` и счётчиков `get_metrics()`. Debounce ему не нужен — latch срабатывает один раз за сессию (решение D2). См. §45.
