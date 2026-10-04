# Live Trading Architecture

Live trading is the **read-mostly sandbox-first** extension of the paper system. It runs the
same entry logic as paper (one unified `StrategyEvaluator` from
`backend/app/analytics/strategy_engine.py`) against the live 1min stream from
`online_candles_1min` + `online_orderbook_aggregates`, but routes executions through
`TinkoffSandboxClient` instead of the paper emulator. Real-money brokerage is **not wired**:
the broker client is hard-bound to sandbox endpoints and never reaches production REST.

Processes (started via `start_processes.sh` under an opt-in gate):

1. **Data Refresher / Streaming / Signal Engine** — identical to paper (see
   `docs/strategy/paper-trading.md`).
2. **Live Engine** (`app/analytics/live_engine.py`): builds the 4h context via
   `build_strategy_context`, then feeds live 1min bars into per-ticker `StrategyEvaluator`
   instances. Emits to `trading.alerts` just like paper, so signal dedup and A/B arms stay
   identical.
3. **Live Executor** (`app/analytics/live_executor.py`): consumes the alerts, sizes positions
   via `calculate_position_size`, and drives the sandbox order book. A take-profit is posted
   as a resting sell limit; a stop-loss is a **synthetic trigger** — the matching sell limit is
   submitted only after the monitored price touches the stop (a sell limit below market would
   execute immediately and is not a stop order).
4. **Preflight** (`app/analytics/live_executor_preflight.py`): read-only canary run before the
   first sandbox canary. Verifies the paper processes are alive
   (`run_data_refresher` / `run_online_data` / `run_live_engine` / `run_paper_trader`), the
   locked strategy matches `EXPECTED_LOCKED_STRATEGY`, and the sandbox account / universe are
   consistent. Fails closed if anything is off.

## Safety contract

- `sandbox_enabled=false` anywhere in the pipeline is a hard stop: no outbound order REST.
  The executor refuses to start; the API endpoints keep serving reads.
- The broker client is instantiated as `TinkoffSandboxClient` only; there is no production
  client class in this repo. Switching the live contour to real money is a separate project,
  not a config flip.
- All outbound broker calls go through a `TokenBucket` rate limiter (in `live_executor.py`);
  retries are bounded and logged.

## Position lifecycle

- Signal from `live_engine` → **pending** (executor sizes, submits a buy at market or posts a
  resting buy limit at the signal price).
- Fill → **open**; executor posts a sell limit at take and records the synthetic stop trigger
  at `stop_price`.
- Exit:

## Monitoring API (Issue #149)

`app/api/live_trading_jobs.py` exposes two read-only endpoints:

- `GET /api/live-trading/positions` — paginated list over `trading.live_positions`. Query:
  `status` (see the table below), `ticker`, `date_from`/`date_to` (applied to
  `COALESCE(signal_ts, created_at)`), `limit` (≤ 1000), `offset`, `sort_by`/`sort_dir`
  (whitelist: `signal_ts`/`entry_ts`/`exit_ts`/`entry_price`/`exit_price`/`pnl_rub`/
  `pnl_pct`/`ticker`/`status`/`created_at`/`id`). Open positions are enriched with
  `current_price` from the latest `online_orderbook_aggregates` snapshot and a recomputed
  `pnl_rub`/`pnl_pct`; closed positions keep their stored `exit_price`. Trailing columns
  returned when present: `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`.
  All `Decimal` values serialise as strings; timestamps are ISO-8601; `NaN`/`NaT`/`None`
  collapse to JSON `null`.
- `GET /api/live-trading/dynamics` — cumulative PnL series over 1h/1d/1w buckets, closed
  positions only. Wins use the paper convention:
  `status='closed_take' OR (status='closed_trailing' AND pnl_rub > 0)`.

`app/api/notifications.py` adds a third read-only probe for ops:

- `GET /api/notifications/status` — short-lived Telegram connectivity check. Returns
  `{status: "connected" | "disconnected", configured: bool, checked_at: ISO}`. The response is
  cached for 30 seconds and **never** exposes token / chat id. If `telegram.enabled=false`
  the payload is `{"status": "disconnected", "configured": false, ...}`.

`status` filter semantics, shared with the paper endpoints:

| value | expands to |
|---|---|
| `closed` | `closed_stop OR closed_take OR closed_trailing` |
| `closed_stop` / `closed_take` / `closed_trailing` | exact match |
| `open` / `pending` / `cancelled` | exact match |

## Stepped trailing stop

The ladder itself is the same pure function used by backtest, walk-forward and paper
(`backend/app/analytics/trailing_stop.py`, grounded by Issue #145). For the live contour:

- **Schema**: `trading.live_positions` has `trailing_enabled`, `trailing_steps` (JSONB),
  `current_stop_price`, `step_reached`, `risk_r`, `exit_reason` — populated by migration
  `20260915_002_live_trailing.py` (`ADD COLUMN IF NOT EXISTS` + backfill of historical rows
  to `false`/`NULL`, idempotent on re-run). The same schema is reused by the forthcoming
  production broker wiring (out of scope for #149).
- **Runtime**: the executor advances the ladder bar-by-bar (Issue #151, completed 2026-09-16).
  Every live position with `trailing_enabled=true` reports `current_stop_price`, `step_reached`,
  and `risk_r` as the ladder progresses. Positions without trailing report these as `null`.
- **Reading a run**: `exit_reason='trailing'` on a live position means the ladder fired;
  `exit_reason='stop'` or `'take'` means the initial stop or take filled without trailing.
- **Kill switch**: set `trading.app_settings.trailing_kill_switch = true` to pause all trailing
  advancement without stopping the executor. Armed positions keep their state; new positions
  are not armed until the switch is cleared.

## Tables

- `trading.alerts` — shared with paper (signal JSONB: price, support/take, confirm_close_time,
  window_mode, rr_mode).
- `trading.live_positions` — sandbox positions (entry/exit, PnL, A/B factors, trailing
  columns from #149, runtime columns from #151).
- `trading.live_equity` — sandbox equity curve (capital + realized + unrealized PnL).
- `trading.app_settings` — runtime switches (key-value JSONB, includes `trailing_kill_switch`).

See `testing-rules.md` for the full parameter set and report formats.

  - `closed_take` — the resting sell limit at take fills.
  - `closed_stop` — a 1min candle touches the synthetic stop; executor submits a sell limit
    at best bid to close.
  - `closed_trailing` — same shape as `closed_stop`, but the stop was ratcheted by a trailing
    ladder before firing. **Operational** (Issue #151, completed 2026-09-16): columns exist in
    `trading.live_positions` via migration `20260915_002_live_trailing.py`, and the executor
    drives them through `_apply_trailing()` in `monitor_positions()`.
  - `closed_broker` — the position vanished from the broker account without a recorded exit
    (e.g., manual intervention, broker-side liquidation). Exit price is the last known price.
