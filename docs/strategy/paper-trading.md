# Paper Trading Architecture

The paper trading system emulates live trading on real market data (no real orders).
Four background processes (started via `start_processes.sh`, default duration 1200 min):

1. **Data Refresher** (`app/analytics/data_refresher.py`): every 15 min pulls 1min
   candles from MOEX ISS API into `candles_1min_raw`, aggregates to 30min/1h/4h/1d
   (`candles_aggregated`), updates FIGI from `trading.instruments`.
2. **Streaming** (`app/analytics/online_data.py`): streams 1min candles + order book
   via T-Bank MarketDataServerSideStream into `online_candles_1min` and
   `online_orderbook_aggregates`.
3. **Signal Engine** (`app/analytics/online_signals.py`): 4h levels (from
   `candles_aggregated`) + 1min reversal confirmation (from `online_candles_1min`).
   Emits A/B arms (signal_source x window_mode). Writes to `trading.alerts`.
4. **Paper Trader** (`app/analytics/paper_trader.py`): emulates entries/exits on live
   signals. Entry modes market/limit; stop/take monitored on 1min candles. Writes to
   `paper_positions` and `paper_equity`.

## Position lifecycle

- **market**: signal -> OPEN immediately at best_ask (skipped if entry >= take).
- **limit**: signal -> PENDING (limit at signal price) -> OPEN when a candle touches
  the limit (low <= limit <= high) -> closed_stop (market) / closed_take (limit).
  PENDING -> CANCELLED if not filled within TTL (20 min) or price ran above take.

## A/B factors (per position)

signal_source (base/imbalance) x window_mode (window/always) x rr_mode (all/rr15/rr2)
x entry_mode (market/limit). Dedup: signals by (ticker, source, window, confirm candle);
positions by signal_id and by (ticker, source, window, rr, entry).

## Catch-up on startup

`app/analytics/position_catchup.py` retroactively processes **pending and open**
positions against historical 1min candles (MOEX), pulling any missing days first.
It mirrors the live paper_trader logic (monitor_pending + monitor_open):

1. **Resolve pending** (scan candles from `limit_ts`):
   - price ran above take before fill -> CANCELLED ('price above take before fill');
   - a candle touches the limit (`low <= limit_price <= high`) -> OPEN (entry at limit price);
   - TTL (20 min) expired without fill -> CANCELLED ('expired').
2. **Check open** (including just-filled; scan candles from `entry_ts`, skip entry candle):
   - `low <= stop` -> closed_stop (market);
   - `high >= take` -> closed_take (limit).

Positions stay consistent whether the trader was running or not.

## Tables

- `trading.alerts` — signals (JSONB details: price, support/take, confirm_close_time, window_mode, rr_mode).
- `trading.paper_positions` — positions (entry/exit, PnL, all A/B factors, signal_id).
- `trading.paper_equity` — portfolio equity curve (capital + realized + unrealized PnL).

See `testing-rules.md` for the full parameter set and report formats.
