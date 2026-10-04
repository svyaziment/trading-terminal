# Архитектура live trading

Live trading — это **read-mostly, sandbox-first** расширение бумажной системы. Оно прогоняет
ту же логику входа, что и paper (единый `StrategyEvaluator` из
`backend/app/analytics/strategy_engine.py`), по живому 1-минутному потоку из
`online_candles_1min` + `online_orderbook_aggregates`, но проводит исполнения через
`TinkoffSandboxClient`, а не через бумажный эмулятор. Реальная брокерская торговля **не
подключена**: клиент брокера жёстко привязан к sandbox-эндпоинтам и никогда не ходит в
продовольственный REST.

Процессы (запускаются через `start_processes.sh` под opt-in-гейтом):

1. **Data Refresher / Streaming / Signal Engine** — те же, что в paper (см.
   `docs/strategy/paper-trading.ru.md`).
2. **Live Engine** (`app/analytics/live_engine.py`): строит 4h-контекст через
   `build_strategy_context`, затем подаёт живые 1min-бары в экземпляры `StrategyEvaluator`
   по тикеру. Пишет в `trading.alerts` ровно как paper, поэтому дедупликация сигналов и А/Б
   ветки остаются идентичными.
3. **Live Executor** (`app/analytics/live_executor.py`): потребляет алерты, считает размер
   позиции через `calculate_position_size` и ведёт книгу заявок в песочнице. Тейк-профит
   выставляется как resting sell limit; стоп-лосс — **синтетический триггер**:
   соответствующая заявка sell limit подаётся только после того, как наблюдаемая цена
   касается стопа (sell limit ниже рынка исполнился бы мгновенно и стоп-ордером не является).
4. **Preflight** (`app/analytics/live_executor_preflight.py`): read-only canary перед первым
   запуском в песочнице. Проверяет, что paper-процессы живы
   (`run_data_refresher` / `run_online_data` / `run_live_engine` / `run_paper_trader`),
   залоченная стратегия совпадает с `EXPECTED_LOCKED_STRATEGY`, а sandbox-аккаунт и юниверс
   согласованы. Закрывает запуск, если что-то не так.

## Контракт безопасности

- `sandbox_enabled=false` в любом месте пайплайна — жёсткий стоп: исходящего REST-трафика к
  брокеру нет. Исполнитель отказывается стартовать; API-эндпоинты продолжают отвечать на
  чтения.
- Клиент брокера инстанцируется только как `TinkoffSandboxClient`; класса для реального
  брокера в этом репозитории нет. Переключение live-контура на реальные деньги — это
  отдельный проект, а не флажок в конфиге.
- Все исходящие вызовы к брокеру проходят через rate-limiter `TokenBucket` (в
  `live_executor.py`); ретраи ограничены и логируются.

## Жизненный цикл позиции

- Сигнал из `live_engine` → **pending** (исполнитель считает размер, подаёт market-buy или
  выставляет resting buy limit по цене сигнала).
- Исполнение → **open**; исполнитель выставляет sell limit на тейке и записывает
  синтетический стоп-триггер на `stop_price`.
- Выход:

## API мониторинга (задача #149)

`app/api/live_trading_jobs.py` отдаёт два read-only эндпоинта:

- `GET /api/live-trading/positions` — постраничный список по `trading.live_positions`.
  Параметры: `status` (см. таблицу ниже), `ticker`, `date_from`/`date_to` (применяются к
  `COALESCE(signal_ts, created_at)`), `limit` (≤ 1000), `offset`, `sort_by`/`sort_dir`
  (белый список: `signal_ts`/`entry_ts`/`exit_ts`/`entry_price`/`exit_price`/`pnl_rub`/
  `pnl_pct`/`ticker`/`status`/`created_at`/`id`). Открытые позиции дополняются
  `current_price` из последнего снимка `online_orderbook_aggregates` и пересчитанными
  `pnl_rub`/`pnl_pct`; закрытые позиции хранят свой `exit_price`. Trailing-колонки
  возвращаются при наличии: `trailing_enabled`, `current_stop_price`, `step_reached`,
  `risk_r`. Все значения `Decimal` сериализуются строками; таймштампы — ISO-8601;
  `NaN`/`NaT`/`None` схлопываются в JSON `null`.
- `GET /api/live-trading/dynamics` — кумулятивный PnL по бакетам 1h/1d/1w, только закрытые
  позиции. Wins считаются по конвенции paper:
  `status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)`.

`app/api/notifications.py` добавляет третий read-only зонд для эксплуатации:

- `GET /api/notifications/status` — короткоживущая проверка связности с Telegram. Возвращает
  `{status: "connected" | "disconnected", configured: bool, checked_at: ISO}`. Ответ кэшируется
  на 30 секунд и **никогда** не отдаёт токен / chat id. Если `telegram.enabled=false`,
  приходит `{"status": "disconnected", "configured": false, ...}`.

Семантика фильтра `status`, общая с paper-эндпоинтами:

| значение | раскрывается в |
|---|---|
| `closed` | `closed_stop OR closed_take OR closed_trailing` |
| `closed_stop` / `closed_take` / `closed_trailing` | точное совпадение |
| `open` / `pending` / `cancelled` | точное совпадение |

## Ступенчатый трейлинг-стоп

Сама лестница — та же чистая функция, что используется бэктестом, walk-forward и paper
(`backend/app/analytics/trailing_stop.py`, закреплена задачей #145). Для live-контура:

- **Схема**: в `trading.live_positions` есть `trailing_enabled`, `trailing_steps` (JSONB),
  `current_stop_price`, `step_reached`, `risk_r`, `exit_reason` — заполнено миграцией
  `20260915_002_live_trailing.py` (`ADD COLUMN IF NOT EXISTS` + backfill исторических строк в
  `false`/`NULL`, идемпотентна при повторном запуске). Та же схема переиспользуется будущей
  проводкой для реального брокера (вне скоупа #149).
- **Рантайм**: исполнитель продвигает лестницу бар за баром (задача #151, завершена 2026-09-16).
  Каждая live-позиция с `trailing_enabled=true` отдаёт `current_stop_price`, `step_reached`
  и `risk_r` по мере продвижения лестницы. Позиции без трейлинга отдают эти поля как `null`.
- **Чтение прогона**: `exit_reason='trailing'` у live-позиции означает, что лестница сработала;
  `exit_reason='stop'` или `'take'` означает, что исполнился начальный стоп или тейк без трейлинга.
- **Kill switch**: установите `trading.app_settings.trailing_kill_switch = true`, чтобы
  приостановить продвижение трейлинга без остановки исполнителя. Armed-позиции сохраняют
  состояние; новые позиции не armed до снятия переключателя.

## Таблицы

- `trading.alerts` — общие с paper (JSONB сигнала: price, support/take,
  confirm_close_time, window_mode, rr_mode).
- `trading.live_positions` — sandbox-позиции (вход/выход, PnL, А/Б-факторы,
  trailing-колонки из #149, рантайм-колонки из #151).
- `trading.live_equity` — кривая капитала sandbox (капитал + реализованный + нереализованный
  PnL).
- `trading.app_settings` — рантайм-переключатели (key-value JSONB, включает `trailing_kill_switch`).

Полный набор параметров и форматы отчётов — в `testing-rules.ru.md`.

  - `closed_take` — resting sell limit на тейке исполнился.
  - `closed_stop` — минутная свеча коснулась синтетического стопа; исполнитель подаёт sell
    limit по best bid для закрытия.
  - `closed_trailing` — та же форма, что у `closed_stop`, но стоп был поджат трейлинг-лестницей
    перед срабатыванием. **Работает** (задача #151, завершена 2026-09-16): колонки есть в
    `trading.live_positions` через миграцию `20260915_002_live_trailing.py`, и исполнитель
    ведёт их через `_apply_trailing()` в `monitor_positions()`.
  - `closed_broker` — позиция исчезла с брокерского счёта без зафиксированного выхода
    (например, ручное вмешательство, ликвидация на стороне брокера). Цена выхода — последняя
    известная цена.
