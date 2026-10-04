# Paper Trading Architecture

The paper trading system emulates live trading on real market data (no real orders).
Four background processes (started via `start_processes.sh`; default duration is until the next session open after 19:00 MSK, so leftover stop/take still have streaming, Issue #137):

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

## Exit rule and the stepped trailing stop (status)

Paper closes a position according to the active `config.trailing_stop` of the locked strategy
(grounded by Issue #148):

- **No trailing / `enabled=false`**: fixed stop/take recorded at entry. `closed_stop` when a 1min
  candle prints `low <= stop_price`, `closed_take` when it prints `high >= take_price`; stop is
  checked first.
- **Trailing `enabled=true` with a valid ladder**: `monitor_open` reads the ladder from the DB
  into an in-memory `trailing_states` map per position, advances it bar-by-bar (ladder order
  *stop → take → arm*; a rung armed by bar *i* bites from bar *i+1*), and persists the current
  ratcheted stop back to `paper_positions.current_stop_price` / `step_reached` / `risk_r` /
  `trailing_enabled` / `trailing_steps` so the state survives a restart. An untouched stop keeps
  `exit_reason='stop'`, a raised one reports `exit_reason='trailing'`, a take fill keeps
  `exit_reason='take'`. Status codes on exit: `closed_stop`, `closed_take`, `closed_trailing`.

The ladder itself is a single pure function in `backend/app/analytics/trailing_stop.py`, shared
with the backtest engine, the `levels_reversal` plugin, the portfolio simulator and walk-forward
(Issue #145). Paper, backtest and walk-forward now read the **same** exit rule whenever the
config enables trailing — so the books are directly comparable.

## Monitoring API (Issue #149)

`app/api/paper_trading_jobs.py` exposes three read-only endpoints used by the dashboard:

- `GET /api/paper-trading/overview` — strategy metadata, per-factor options, summary stats.
  `summary` now carries four trailing-aware fields: `trailing_closed` (count of closed positions
  whose exit was `trailing`), `trailing_closed_pnl_rub` (their summed PnL), `trailing_open`
  (open positions with `trailing_enabled=true`), `active_stop_count` (open positions with a
  finite stop — trailing or not).
- `GET /api/paper-trading/positions` — paginated list. Each row exposes the trailing columns on
  `paper_positions`: `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`, plus
  the existing `exit_reason` (`stop` / `take` / `trailing`). `Decimal` values go out as strings,
  timestamps as ISO-8601, `NaN`/`NaT`/`None` collapse to JSON `null`. `sort_by`/`sort_dir`
  whitelisted; `limit` capped at 1000.
- `GET /api/paper-trading/dynamics` — cumulative PnL series (1h/1d/1w buckets). Wins are
  `status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)`, matching the Live
  convention.

`status` filter semantics, same as in the Live endpoints:

| value | expands to |
|---|---|
| `closed` | `closed_stop OR closed_take OR closed_trailing` |
| `closed_stop` / `closed_take` / `closed_trailing` | exact match |
| `open` / `pending` / `cancelled` | exact match |

`status=closed_trailing` alone is therefore the way to isolate trailing-exit positions without
the plain stops and takes.

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
