# Архитектура paper trading

Система paper trading эмулирует живую торговлю на реальных рыночных данных (без
реальных заявок). Четыре фоновых процесса (запуск через `start_processes.sh`,
длительность по умолчанию 1200 мин):

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

## А/Б факторы (на позицию)

signal_source (base/imbalance) x window_mode (window/always) x rr_mode (all/rr15/rr2)
x entry_mode (market/limit). Dedup: сигналы по (ticker, source, window, свеча
подтверждения); позиции по signal_id и по (ticker, source, window, rr, entry).

## Catch-up при старте

`app/analytics/position_catchup.py` ретроспективно проверяет открытые позиции по
историческим минутным свечам (MOEX) на срабатывание стоп/тейк, произошедшее пока
трейдер не работал, предварительно дотягивая недостающие дни.

## Таблицы

- `trading.alerts` — сигналы (JSONB details: price, support/take, confirm_close_time, window_mode, rr_mode).
- `trading.paper_positions` — позиции (вход/выход, PnL, все А/Б факторы, signal_id).
- `trading.paper_equity` — кривая капитала портфеля (капитал + реализованный + нереализованный PnL).

Полный набор параметров и форматы отчётов — в `testing-rules.ru.md`.
