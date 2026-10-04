# Архитектура paper trading

Система paper trading эмулирует живую торговлю на реальных рыночных данных (без
реальных заявок). Четыре фоновых процесса (запуск через `start_processes.sh`,
длительность по умолчанию — до следующего открытия после 19:00 МСК, чтобы стоп/тейк после закрытия сессии ещё видели стакан, задача #137):

1. **Data Refresher** (`app/analytics/data_refresher.py`): каждые 15 мин тянет
   минутные свечи из MOEX ISS API в `candles_1min_raw`, агрегирует в
   30min/1h/4h/1d (`candles_aggregated`), обновляет FIGI из `trading.instruments`.
2. **Streaming** (`app/analytics/online_data.py`): стримит минутные свечи + стакан
   через T-Bank MarketDataServerSideStream в `online_candles_1min` и
   `online_orderbook_aggregates`.
3. **Signal Engine** (`app/analytics/online_signals.py`): 4h уровни (из
   `candles_aggregated`) + подтверждение разворота на 1min (из `online_candles_1min`).
   Генерирует А/Б ветки (signal_source x window_mode). Пишет в `trading.alerts`.
4. **Paper Trader** (`app/analytics/paper_trader.py`): эмулирует входы/выходы по
   живым сигналам. Режимы входа market/limit; стоп/тейк по 1min свечам. Пишет в
   `paper_positions` и `paper_equity`.

## Жизненный цикл позиции

- **market**: сигнал -> сразу OPEN по best_ask (пропуск, если entry >= take).
- **limit**: сигнал -> PENDING (лимитка по цене сигнала) -> OPEN, когда свеча касается
  лимитки (low <= limit <= high) -> closed_stop (маркет) / closed_take (лимит).
  PENDING -> CANCELLED, если не исполнилась за TTL (20 мин) или цена ушла выше тейка.

## Правило выхода и ступенчатый трейлинг-стоп (статус)

Paper закрывает позицию согласно активному `config.trailing_stop` залоченной стратегии
(закреплено задачей #148):

- **Без трейлинга / `enabled=false`**: фиксированные стоп/тейк, записанные на входе.
  `closed_stop`, если минутная свеча печатает `low <= stop_price`, и `closed_take`, если
  `high >= take_price`; стоп проверяется первым.
- **Трейлинг `enabled=true` с валидной лестницей**: `monitor_open` читает лестницу из БД
  в in-memory-карту `trailing_states` по позиции, продвигает её бар за баром (порядок
  в лестнице *стоп → тейк → вооружение*; ступень, вооружённая баром *i*, кусается с бара *i+1*),
  и пишет текущий поджатый стоп обратно в `paper_positions.current_stop_price` / `step_reached` /
  `risk_r` / `trailing_enabled` / `trailing_steps`, чтобы состояние переживало рестарт.
  Нетронутый стоп оставляет `exit_reason='stop'`, поджатый — `exit_reason='trailing'`, исполнение
  по тейку — `exit_reason='take'`. Коды статусов на выходе: `closed_stop`, `closed_take`,
  `closed_trailing`.

Сама лестница — одна чистая функция в `backend/app/analytics/trailing_stop.py`, общая с движком
бэктеста, плагином `levels_reversal`, портфельным симулятором и walk-forward (задача #145). Paper,
бэктест и walk-forward теперь читают **одно и то же** правило выхода, как только трейлинг включён
в конфиге — книги можно сравнивать напрямую.

## API мониторинга (задача #149)

`app/api/paper_trading_jobs.py` отдаёт три read-only эндпоинта для дашборда:

- `GET /api/paper-trading/overview` — метаданные стратегии, опции по факторам, сводная
  статистика. `summary` теперь содержит четыре trailing-поля: `trailing_closed` (количество
  закрытых позиций с выходом `trailing`), `trailing_closed_pnl_rub` (их суммарный PnL),
  `trailing_open` (открытые позиции с `trailing_enabled=true`), `active_stop_count` (открытые
  позиции с конечным стопом — как trailing, так и обычные).
- `GET /api/paper-trading/positions` — постраничный список. Каждая строка отдаёт
  trailing-колонки из `paper_positions`: `trailing_enabled`, `current_stop_price`,
  `step_reached`, `risk_r`, плюс существующий `exit_reason` (`stop` / `take` / `trailing`).
  Значения `Decimal` уходят строками, таймштампы — ISO-8601, `NaN`/`NaT`/`None` схлопываются в
  JSON `null`. `sort_by`/`sort_dir` по белому списку; `limit` не больше 1000.
- `GET /api/paper-trading/dynamics` — кумулятивный PnL по бакетам 1h/1d/1w. Win считается как
  `status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)` — конвенция Live.

Семантика фильтра `status` — та же, что в Live-эндпоинтах:

| значение | раскрывается в |
|---|---|
| `closed` | `closed_stop OR closed_take OR closed_trailing` |
| `closed_stop` / `closed_take` / `closed_trailing` | точное совпадение |
| `open` / `pending` / `cancelled` | точное совпадение |

Поэтому `status=closed_trailing` — это способ выбрать позиции с трейлинг-выходом отдельно от
обычных стопов и тейков.

## А/Б факторы (на позицию)

signal_source (base/imbalance) x window_mode (window/always) x rr_mode (all/rr15/rr2)
x entry_mode (market/limit). Dedup: сигналы по (ticker, source, window, свеча
подтверждения); позиции по signal_id и по (ticker, source, window, rr, entry).

## Catch-up при старте

`app/analytics/position_catchup.py` ретроспективно обрабатывает позиции в статусах
**pending и open** по историческим минутным свечам (MOEX), предварительно дотягивая
недостающие дни. Логика повторяет живой paper_trader (monitor_pending + monitor_open):

1. **Разрешение pending** (скан свечей с `limit_ts`):
   - цена ушла выше тейка до исполнения -> CANCELLED ('price above take before fill');
   - свеча касается лимитки (`low <= limit_price <= high`) -> OPEN (вход по цене лимитки);
   - TTL (20 мин) истёк без исполнения -> CANCELLED ('expired').
2. **Проверка open** (включая только что исполненные; скан свечей с `entry_ts`, свеча входа пропускается):
   - `low <= stop` -> closed_stop (маркет);
   - `high >= take` -> closed_take (лимит).

Позиции остаются согласованными независимо от того, работал трейдер или нет.

## Таблицы

- `trading.alerts` — сигналы (JSONB details: price, support/take, confirm_close_time, window_mode, rr_mode).
- `trading.paper_positions` — позиции (вход/выход, PnL, все А/Б факторы, signal_id).
- `trading.paper_equity` — кривая капитала портфеля (капитал + реализованный + нереализованный PnL).

Полный набор параметров и форматы отчётов — в `testing-rules.ru.md`.
