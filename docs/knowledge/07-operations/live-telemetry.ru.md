# Telegram-алертинг и метрики live-контура (задача #177, эпик #172, блок E)

> **Source:** project-context.ru.md sections 23 + handover.ru.md sections 45
> **Last refreshed:** 2026-10-04, task-346

## 23. Telegram-алертинг и метрики live-контура (задача #177, эпик #172, блок E)

Завершено 2026-09-27. Live-исполнитель теперь сообщает о себе наружу: событийные
Telegram-алерты, периодический heartbeat и персистентный снимок метрик, который читает
`GET /api/live-trading/metrics`. Paper-контур (`paper_trader`) и сам `TelegramNotifier` не
изменены — live использует нотификацию как библиотеку. Операционные детали и команды —
`handover.ru.md` §45.

**Почему так**: до #177 всё, что знал live-контур, оставалось в логе контейнера, а пульс
#174 жил только в памяти процесса, поэтому «исполнитель упал» и «исполнитель простаивает»
были неразличимы. Персистенция снимка (решение D1) выбрана в `trading.app_settings` одной
JSONB-строкой `live_executor_metrics` — без новой таблицы и без миграции: ключ (21 символ)
укладывается в `key VARCHAR(64)`, а `value JSONB NOT NULL` подходит как есть;
`test_live_schema.py` сверяет только `REQUIRED_APP_SETTINGS_KEYS`, поэтому новый ключ его не
ломает.

**Конфигурация** (`trading_config.py`, секция `LIVE_ALERTING` + `get_live_alerting_config()`):
- `heartbeat_interval_seconds`: `3600`, env `LIVE_HEARTBEAT_INTERVAL_SECONDS`, диапазон `(1, 86400]`.
- `heartbeat_stale_seconds`: `300`, env `LIVE_HEARTBEAT_STALE_SECONDS`, диапазон `(1, 86400]`.
- `alert_debounce_seconds`: `300`, env `LIVE_ALERT_DEBOUNCE_SECONDS`, диапазон `(0, 86400]`.
- `slippage_alert_bp`: `50.0`, env `LIVE_SLIPPAGE_ALERT_BP`, диапазон `(0, 10000]`.
- `max_consecutive_errors`: `5`, env `LIVE_MAX_CONSECUTIVE_ERRORS`, диапазон `[1, 100]` —
  источник истины вместо жёсткой константы `MAX_CONSECUTIVE_ERRORS` (решение D3; константа
  осталась fallback-дефолтом, поведение без конфига не меняется).
- `metrics_flush_seconds`: `300`, env `LIVE_METRICS_FLUSH_SECONDS`, диапазон `(1, 86400]`.
- `metrics_key`: `live_executor_metrics` — контракт имени строки, env не переопределяется.
- `telegram_alerts_enabled`: `true`, env `LIVE_TELEGRAM_ALERTS` — мастер-переключатель;
  `false` оставляет все события только в логе.

Непарсимое или внедиапазонное значение env бросает `ValueError` в момент чтения (та же
конвенция, что у `LIVE_RISK` из #176), поэтому опечатка в `.env` не может молча заглушить
алерт оператора. In-memory override `LiveExecutor(alerting={...})` проходит ровно те же
границы через `validate_live_alerting_values()`.

**Креденшелы** (решение D4): новых env-переменных нет. Источник —
`config_manager.load_settings().telegram`, то есть `TGM_TOKEN` / `TGM_CHAT_ID` (legacy-имя
`TGM_CHAT`) из окружения либо `backend/config/settings.yaml`; его же используют
`run_paper_trader` и `/api/notifications/status`. `build_default_notifier()` возвращает
`None`, если креденшелов нет или они не читаются, — исполнитель всё равно стартует и ведёт
события в логе, потому что дыра в мониторинге не имеет права блокировать торговый контур.

**Отправка** — `LiveExecutor._notify(event, title, lines, critical=False, dedupe=False, icon=None)`:
никогда не бросает исключение в торговый цикл, no-op без нотари или при выключенном мастере,
ведёт счётчики `alerts_attempted/sent/failed/suppressed/skipped_total`. Debounce (решение D2)
применяется только к повторяющимся критическим (`equity_snapshot_error`,
`protection_failed:{ticker}`, `invariant_violation:{ticker}` — оба инварианта,
`trailing_amend_failed:{ticker}`, `oco_orphan`, `oco_orphan_unverified`), ключ включает
тикер, поэтому окна разных бумаг независимы. Редкие и one-shot события — старт/стоп, вход,
выход с проскальзыванием, перенос стопа, kill switch, `risk_breach` и его снятие,
heartbeat — доставляются всегда, поэтому «процесс жив» доходит и во время шторма
критических. Текст рендерит `_alert_text()`, где каждое значение проходит
`escape_markdown`: `send_message` всегда запрашивает `parse_mode=Markdown`, а подчёркивание
в тикере или в брокерской ошибке иначе превратило бы сообщение в 400.

**Heartbeat**: `_maybe_send_heartbeat()` вызывается раз за цикл `run()` и отправляет 💓
«Live-контур жив» не чаще `heartbeat_interval_seconds`; доставленный heartbeat сразу делает
`_flush_metrics(force=True)`, поэтому API никогда не показывает счётчики старше последнего
«процесс жив». Значение интервала `<= 0` отключает отправку, оставляя in-memory `heartbeat_ts`
из #174.
**Персистенция** (решение D5): `_flush_metrics()` пишет снимок не каждый цикл, а по
`metrics_flush_seconds`, плюс принудительно в трёх точках — доставленный heartbeat, переход
kill switch и graceful shutdown (уже после освобождения advisory lock). Причина: при
`check_interval_seconds=30` запись каждый цикл дала бы ~2880 UPDATE/сутки в таблицу, которую
каждый цикл читает и пишет `_refresh_risk_breach_reset`. Снимок — это `get_metrics()`
(#174–#176) плюс provenance-поля (`schema_version=1`, `persisted_at`, `strategy`, `tickers`,
`notifier_configured`, окна heartbeat, счётчики алертов и flush-ов, `kill_switch_source`).
Сбой записи увеличивает `metrics_flush_errors_total`, пишет warning и не пробрасывается в
цикл.

**Эндпоинт `GET /api/live-trading/metrics`** (`live_trading_jobs.py`) читает строку
`app_settings`, вычисляет свежесть **на чтении** (`source.age_seconds` и `flush_stale` при
возрасте больше `2 × metrics_flush_seconds`; `heartbeat.age_seconds` и `stale` по окну из
снимка с откатом на действующий конфиг), читает kill switch из живой строки
`trading.trailing_kill_switch` с приоритетом над снимком, добавляет открытые позиции из
`trading.live_positions` (`open_total`, `protected_total`, `unprotected_total`,
`unprotected_tickers`, `trailing_total`, `items[]`, где защищённость — факт `broker_stop_id`
из #175) и публикует `state` одним словом в порядке `unknown` → `kill_switch` →
`no_heartbeat` → `stale` → `error_threshold` → `risk_breach` → `running`. Рядом лежат
`risk.limits` (действующий конфиг) и `risk.snapshot_limits` (то, с чем реально работал
исполнитель): расхождение означает устаревшие лимиты в процессе, и это видно, а не спрятано.

С задачи #200 `loop.stopped_reason` сообщает, **почему** завершился цикл исполнителя
(`max_consecutive_errors` / `session_end` / `duration` / `signal` / `exception`, `null`, пока он
ещё работает). Поле появилось потому, что у контура, остановившегося на серии ошибок, по
определению нет heartbeat, и автомат `state` способен выдать лишь `stale` — а ровно так же
выглядит и убитый процесс. `stopped_reason` и есть различие между «исполнитель сам остановился,
защитив счёт» и «исполнитель умер».

Отсутствующий, битый или пустой снимок — **не ошибка**: `available=false` плюс `reason`
(`no_snapshot` / `malformed_snapshot` / `empty_snapshot`), потому что мониторинговый
эндпоинт, падающий в 500, хуже того, который честно говорит «данных нет». `503` остаётся
единственным жёстким сбоем — когда `trading.app_settings` не читается вовсе.

**Два подводных камня чтения**, найденные только на реальной БД (все unit-тесты на фейках
были зелёные):
1. `SelectResult.to_dataframe()` нормализует JSONB через `astype(str)`, поэтому снимок
   приходит Python-repr'ом (`"{'schema_version': 1, ...}"` — одинарные кавычки,
   `True`/`None` вместо `true`/`null`) и `json.loads` падает → вечный `malformed_snapshot`
   в бою. `_loads_metrics_text()` пробует `json.loads`, затем `ast.literal_eval` (разбирает
   только литералы, кода не исполняет); битый repr по-прежнему `malformed_snapshot`.
2. pandas отдаёт SQL `NULL` как `NaN`/`pd.NA`, и без guard'а отсутствующий `broker_stop_id`
   публиковался строкой `"nan"`, а `_metrics_bool(nan)` давал `trailing_enabled=true` —
   незащищённая позиция выглядела защищённой. `_metrics_is_missing()` (None/NaN/NA/NaT, без
   импорта pandas) применён во всех coercer'ах `_metrics_int/_float/_bool/_text/_datetime` и
   в фильтре `status`.

**Анти-дрейф**: `_METRICS_SNAPSHOT_FIELDS` — единственный список потребляемых полей, всё
остальное публикуется в `extra` через `_json_safe`, поэтому новое поле исполнителя не
теряется молча. Тест `test_the_reader_covers_every_key_the_executor_persists` прогоняет
настоящий `_flush_metrics(force=True)` и требует `set(written) <= _METRICS_SNAPSHOT_FIELDS`
и `extra == {}`, а `test_the_executor_snapshot_survives_the_dataframe_round_trip`
воспроизводит repr-путь `to_dataframe()`. Веб-слой намеренно **не** импортирует
`app.analytics.live_executor`: общий контракт — имя строки из `LIVE_ALERTING.metrics_key`, а
не код торгового цикла.

**Отказоустойчивость**: ни алерт, ни запись снимка не могут остановить торговлю — оба пути
обёрнуты в `try/except`, пишут warning и увеличивают свой счётчик ошибок. `alerts_failed_total`
и `metrics_flush_errors_total` — то, за чем должен следить оператор, а не за циклом.

**Тестирование**: `backend/tests/test_live_alerting.py` (167 тестов) покрывает дефолты и
env-override `LIVE_ALERTING`, валидацию диапазонов и `validate_live_alerting_values()`,
гарантии `_notify()` (исключение из нотари, `enabled=False`, выключенный мастер, debounce по
ключу события), каждый событийный хук, heartbeat и его throttle, `_flush_metrics()`
(throttle, force-точки, сбой записи), полный ответ эндпоинта и все его деградации. Полный
прогон `backend/tests` — 765 passed.

**SSL-сертификаты для T-Bank gRPC:** на хосте Windows требуется явно задать `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH="$(pwd)/backend/certs/tbank-root.pem"` (автоматически устанавливается в `start_processes.sh`), иначе gRPC-подключение к `sandbox-invest-public-api.tbank.ru` падает с `CERTIFICATE_VERIFY_FAILED`.

## 45. Эксплуатация Telegram-алертинга и мониторинга live-контура (задача #177)

### Зачем

До #177 live-исполнитель сообщал о себе только в лог контейнера: снятый стоп,
нарушенный инвариант или взведённая блокировка просадки оставались незамеченными, а
«процесс умер» и «процесс простаивает» выглядели одинаково — пульс #174 жил в памяти и
умирал вместе с процессом. #177 добавляет три слоя: событийные Telegram-алерты,
периодический heartbeat и персистентный снимок метрик с HTTP-читателем
`GET /api/live-trading/metrics`. Архитектура — `project-context.ru.md` §23.

### Что изменилось

- `backend/app/analytics/trading_config.py` — секция `LIVE_ALERTING` (8 ключей),
  `LIVE_ALERTING_BOUNDS`, `LIVE_ALERTING_ENV`, `get_live_alerting_config()`,
  `get_live_alerting_bounds()`, `validate_live_alerting_values()`.
- `backend/app/analytics/live_executor.py` — `_alert_text()`, `_notify()`,
  `_maybe_send_heartbeat()`, `_metrics_payload()`, `_flush_metrics()`,
  `build_default_notifier()`, `run_live_executor()`; параметры конструктора `notifier=`
  и `alerting=`; 21 точка вызова `_notify()` (20 ключей событий);
  `_max_consecutive_errors` из конфига вместо жёсткой константы (решение D3);
  `_kill_switch_source`; константы `LIVE_METRICS_KEY = "live_executor_metrics"` и
  `LIVE_METRICS_SCHEMA_VERSION = 1`.
- `backend/app/api/live_trading_jobs.py` — `GET /api/live-trading/metrics`
  (`_metrics_endpoint_payload()` и `_metrics_*`-helper'ы). Модуль
  `app.analytics.live_executor` здесь намеренно не импортируется: веб-слой не должен
  зависеть от торгового цикла, общий контракт — ключ строки `app_settings`.
- `backend/tests/test_live_alerting.py` — 167 тестов: конфиг и валидация, `_notify`
  и debounce, каждый событийный хук, heartbeat, flush снимка, весь ответ endpoint'а
  и деградации.
- `backend/app/notifications/telegram_notifier.py` — `_escape_markdown` стал публичным
  `escape_markdown` (приватный алиас сохранён для прежних вызовов): live-контур строит текст
  алерта сам и обязан экранировать тикеры, причины и строки брокерских ошибок точно так же,
  как это делают paper-хелперы.
- `start_processes.sh` — запуск исполнителя переведён с `LiveExecutor().run(...)` на
  `run_live_executor(...)`, поэтому в штатном запуске (включая `SESSION_AWARE=1`) нотари
  подключается и алерты действительно уходят.
- `.env.example` — блок из 7 переменных `LIVE_*`.

### Конфигурация (`LIVE_ALERTING`, все ключи необязательны)

| Ключ | Env | Дефолт | Диапазон | Назначение |
|---|---|---|---|---|
| `heartbeat_interval_seconds` | `LIVE_HEARTBEAT_INTERVAL_SECONDS` | 3600 | (1, 86400] | Период Telegram-heartbeat; значение `<= 0` отключает отправку |
| `heartbeat_stale_seconds` | `LIVE_HEARTBEAT_STALE_SECONDS` | 300 | (1, 86400] | Окно, после которого пульс считается устаревшим |
| `alert_debounce_seconds` | `LIVE_ALERT_DEBOUNCE_SECONDS` | 300 | (0, 86400] | Минимальный интервал между двумя алертами одного ключа (только `dedupe=True`) |
| `slippage_alert_bp` | `LIVE_SLIPPAGE_ALERT_BP` | 50.0 | (0, 10000] | Порог проскальзывания выхода, bp |
| `max_consecutive_errors` | `LIVE_MAX_CONSECUTIVE_ERRORS` | 5 | [1, 100] | Порог ошибок подряд: останов цикла + critical-алерт (решение D3) |
| `metrics_flush_seconds` | `LIVE_METRICS_FLUSH_SECONDS` | 300 | (1, 86400] | Период плановой записи снимка метрик (решение D5) |
| `metrics_key` | — | `live_executor_metrics` | 1..64 символа | Ключ строки `trading.app_settings`; контракт, а не тюнинг |
| `telegram_alerts_enabled` | `LIVE_TELEGRAM_ALERTS` | `true` | bool | Мастер-переключатель: `false` оставляет все события только в логе |

Непарсимое или внедиапазонное значение env бросает `ValueError` в момент чтения, поэтому
опечатка в `.env` падает сразу, а не молча глушит алерт оператора. `LiveExecutor`
принимает in-memory override `alerting=`, который проходит ровно те же границы через
`validate_live_alerting_values()`.

Креденшелы Telegram — **прежние** (решение D4, новых env не вводили):
`config_manager.load_settings().telegram`, то есть `TGM_TOKEN` и `TGM_CHAT_ID`
(legacy-имя `TGM_CHAT`) из окружения либо `backend/config/settings.yaml`.
`build_default_notifier()` возвращает `None`, если креденшелов нет или они не читаются, —
исполнитель всё равно стартует и ведёт события в логе.

### События

Доставляются всегда (`dedupe=False`, редкие или one-shot): `live_start`, `live_stop`,
`heartbeat`, `live_entry`, `live_exit`, `slippage_high:{ticker}`, `trailing_step`,
`protection_pending:{ticker}`, `kill_switch_on` / `kill_switch_off`, `risk_breach`,
`risk_breach_cleared`, `risk_breach_restored`, `consecutive_errors`. `risk_breach`
намеренно без debounce: latch срабатывает один раз за сессию, а «процесс жив» обязан
доходить даже во время шторма критических алертов.

Подавляются окном `alert_debounce_seconds` (`dedupe=True`, повторяются каждый цикл, пока
сбой длится): `equity_snapshot_error`, `protection_failed:{ticker}`,
`invariant_violation:{ticker}` (оба инварианта), `trailing_amend_failed:{ticker}`,
`oco_orphan`, `oco_orphan_unverified`. Ключ debounce для тикерных событий включает тикер,
поэтому окно у SBER и PLZL независимые.

Сообщения рендерит `_alert_text()`: каждое динамическое значение проходит через
`escape_markdown`, потому что `TelegramNotifier.send_message` всегда запрашивает
`parse_mode=Markdown`. Без экранирования подчёркивание в тикере или в тексте брокерской
ошибки превращало бы сообщение в 400, и оператор не получил бы ничего. `None`-поля
выбрасываются, поэтому вызывающий код передаёт опциональные поля безусловно.

### Персистенция снимка (решения D1 + D5)

`_flush_metrics()` делает upsert одной JSONB-строки `trading.app_settings` по ключу
`metrics_key` — без новой таблицы и без миграции. Вне расписания (`force=True`) снимок
пишется в трёх точках, которые оператор не должен потерять: доставленный heartbeat,
переход kill-switch и graceful shutdown (уже после освобождения advisory lock — это
последний шанс оставить правдивые счётчики). Плановая запись выполняется один раз за
цикл `run()` и не чаще `metrics_flush_seconds`: при `check_interval_seconds=30` запись
каждый цикл дала бы ~2880 UPDATE/сутки в таблицу, которую каждый цикл читает и пишет
`_refresh_risk_breach_reset` (#176).

Снимок — это `get_metrics()` (#174–#176) плюс provenance-поля: `schema_version`,
`persisted_at`, `strategy`, `tickers`, `ticker_count`, `notifier_configured`,
`telegram_alerts_enabled`, `max_consecutive_errors`, окна heartbeat, счётчики
`alerts_attempted/sent/failed/suppressed/skipped_total`, `heartbeats_sent_total`,
`metrics_flushes_total`, `metrics_flush_errors_total`, `kill_switch` и
`kill_switch_source`.

Сбой записи увеличивает `metrics_flush_errors_total`, пишет warning и **не**
пробрасывается в цикл и не трогает `_consecutive_errors`: мониторинг не имеет права
останавливать торговлю.

### Эндпоинт `GET /api/live-trading/metrics`

Ответ: `available`, `reason`, `error`, `state`, `generated_at`, `source`, `loop`,
`heartbeat`, `kill_switch`, `protection`, `risk`, `alerting`, `positions`, `extra`.

- `state` — одно слово в фиксированном порядке: `unknown` (нет читаемого снимка) →
  `kill_switch` → `no_heartbeat` → `stale` → `error_threshold` → `risk_breach` →
  `running`. Порядок намеренный: сначала «жив ли процесс вообще», затем торговые
  состояния.
- Отсутствующий или битый снимок — **не ошибка**: `available=false` плюс `reason`
  (`no_snapshot` / `malformed_snapshot` / `empty_snapshot`). `503` возвращается только
  когда `trading.app_settings` не читается вовсе — тогда отдавать нечего.
- Возраст считается на чтении, а не берётся из снимка: `source.age_seconds` и
  `source.flush_stale` (старше `2 × metrics_flush_seconds`), `heartbeat.age_seconds` и
  `heartbeat.stale` (окно из снимка с откатом на действующий конфиг). Aware-метки
  сворачиваются в naive MSK тем же `now_msk_naive()`, что и в исполнителе, поэтому
  возраст не бывает отрицательным.
- `kill_switch` читается из живой строки `trading.trailing_kill_switch` и имеет приоритет
  над снимком; публикуются `active`, `snapshot_active`, `from_live_row` и `source`
  (`startup` / `app_settings` / `app_settings:missing_key` / `db_error:<Type>`).
- `positions` — отдельный запрос к `trading.live_positions`: `open_total`,
  `protected_total`, `unprotected_total`, `unprotected_tickers`, `trailing_total`,
  `items[]`. Защищённость определяется фактом `broker_stop_id` (#175), трейлинг считается
  отдельно. Сбой чтения этой таблицы деградирует только данный блок, а не весь ответ.
- `risk.limits` — действующий конфиг, `risk.snapshot_limits` — то, с чем реально работал
  исполнитель; расхождение означает, что процесс торгует на устаревших лимитах, и панель
  обязана это показать, а не спрятать.
- `extra` — анти-дрейф: публикуется всё, чего нет в `_METRICS_SNAPSHOT_FIELDS`. Новое
  поле исполнителя видно сразу, а не теряется молча; тест
  `test_the_reader_covers_every_key_the_executor_persists` прогоняет настоящий
  `_flush_metrics(force=True)` и требует `set(written) <= _METRICS_SNAPSHOT_FIELDS` и
  `extra == {}`.
### Что обязан знать эксплуатант

- **`to_dataframe()` нормализует JSONB в Python-repr.** `DBManager.select()` отдаёт
  словарь как есть, но `.to_dataframe()` применяет `astype(str)`, и снимок приходит
  строкой вида `"{'schema_version': 1, ...}"` — одинарные кавычки, `True`/`None` вместо
  `true`/`null`. `json.loads` такое не парсит, поэтому читатель использует
  `_loads_metrics_text()`: сначала `json.loads`, затем `ast.literal_eval` (разбирает
  только литералы, кода не исполняет); битый repr по-прежнему даёт `malformed_snapshot`.
  **Любой новый читатель JSONB обязан делать то же**, иначе в бою будет вечный
  `malformed_snapshot` при полностью зелёных unit-тестах на фейках — именно так этот баг
  и выглядел до smoke в контейнере.
- **pandas отдаёт SQL `NULL` как `NaN` / `pd.NA`.** Без guard'а отсутствующий
  `broker_stop_id` публиковался бы строкой `"nan"`, а `_metrics_bool(nan)` давал бы
  `trailing_enabled=true` — незащищённая позиция выглядела защищённой.
  `_metrics_is_missing()` (None / NaN / NA / NaT, без импорта pandas) применён во всех
  `_metrics_int` / `_float` / `_bool` / `_text` / `_datetime` и в фильтре `status` блока
  позиций.
- Алерты **никогда** не роняют цикл: `_notify()` не бросает исключений, сбой доставки
  увеличивает `alerts_failed_total` и остаётся в логе, ретраев нет. Без нотари или при
  `LIVE_TELEGRAM_ALERTS=false` это no-op — растёт `alerts_skipped_total`.
- Счётчики алертов и heartbeat живут в памяти процесса и обнуляются при рестарте.
  Источник истины «как давно» — `source.age_seconds` и `heartbeat.age_seconds`, а не
  счётчики.
- `heartbeat_interval_seconds=3600` при `heartbeat_stale_seconds=300` означает, что в
  штатном режиме пульс устаревает между отправками: `heartbeat.stale=true` сам по себе не
  инцидент. Инцидент — `state=stale` / `no_heartbeat` вместе с растущим
  `source.age_seconds` и `flush_stale=true`. Для «зелёного» мониторинга выставьте
  `LIVE_HEARTBEAT_STALE_SECONDS` больше интервала отправки.
- Эндпоинт не отличает «исполнитель остановлен намеренно» от «исполнитель упал»:
  `available=true` сохраняется от последнего flush, а `state` становится `stale`.
  Внешний watcher строится на `state` + возрасте, не на `available`.
- `LIVE_TELEGRAM_ALERTS=false` глушит события, но снимок метрик продолжает писаться:
  `metrics_key` и flush от мастер-переключателя не зависят.
- **Точка входа имеет значение.** `run_live_executor()` подключает нотари через
  `build_default_notifier()`, а прямой `LiveExecutor().run(...)` — нет: такой процесс
  торгует и пишет снимок метрик, но все события остаются только в логе. Canary-скрипт с
  реальной доставкой в чат обязан использовать `run_live_executor(...)` либо явно передать
  `notifier=build_default_notifier()`. `start_processes.sh` переведён на `run_live_executor`.
### Команды

```bash
# Основной запрос мониторинга
curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool

# Состояние и возраст одной строкой
curl -s http://localhost:8000/api/live-trading/metrics | python -c "import sys,json; d=json.load(sys.stdin); print(d['available'], d['state'], d['source']['age_seconds'], d['heartbeat']['age_seconds'], d['positions']['unprotected_total'])"

# Сырой снимок в БД
psql -c "SELECT key, updated_at, jsonb_pretty(value) FROM trading.app_settings WHERE key='live_executor_metrics';"

# Read-only проверка Telegram-подключения (getMe, кеш 30 с)
curl -s http://localhost:8000/api/notifications/status | python -m json.tool

# Прогон без Telegram: все события остаются в логе
LIVE_TELEGRAM_ALERTS=false python -m app.analytics.live_executor 1

# Canary: короткий прогон с частым heartbeat и частой записью снимка
LIVE_HEARTBEAT_INTERVAL_SECONDS=30 LIVE_METRICS_FLUSH_SECONDS=10 \
  python -m app.analytics.live_executor 1

# Тесты (160)
cd backend && python -m pytest tests/test_live_alerting.py -q
```

### Известные ограничения

- Снимок пишет только живой процесс: при остановленном исполнителе `available=true`
  остаётся от последнего flush, а данные устаревают. Таблицы истории метрик нет
  (решение D1 — одна строка на процесс).
- История самих Telegram-сообщений нигде не хранится — только счётчики доставки.
- Ретраев доставки нет: недоставленный critical остаётся в логе контейнера.
- Окно debounce живёт в памяти (`_alert_last_sent`), поэтому рестарт обнуляет его и может
  пропустить дубль одного и того же critical сразу после старта.
- Heartbeat отправляется только из рабочего цикла `run()`; отдельного процесса-сторожа
  (внешний watchdog с алертом «снимок не обновлялся N минут») в #177 нет — деплой-шаг и
  healthcheck относятся к блоку F (#178).
- Имена полей снимка — контракт, а не производная от кода: читатель не импортирует
  `live_executor`, поэтому новое поле нужно добавлять и в `_METRICS_SNAPSHOT_FIELDS`,
  иначе оно уедет в `extra` (безопасно, но не типизировано).

Тайминг алерта о старте (фикс от 2026-09-27, follow-up к #177): алерт `live_start` отправляется сразу при старте процесса - до ночного ожидания сессии 10:00 MSK - поэтому воскресный запуск виден в чате немедленно. Поскольку алерт идёт до `initialize()`, число тикеров равно 0 и имя стратегии пусто до открытия сессии; трактуйте эти два поля как «ещё не инициализированы». `check_interval` вычисляется до алерта, поэтому payload не падает.
