# Live equity and risk gates (Issue #176, Epic #172 task D)

> **Source:** project-context.md sections 22 + handover.md sections 44
> **Last refreshed:** 2026-10-04, task-346

## 22. Live equity and risk gates (Issue #176, Epic #172 task D)

Completed 2026-09-27. The live contour now accounts for its own equity and enforces
risk limits before every entry. The paper contour (`paper_equity`, `write_equity`,
`config_manager.RiskConfig`) is untouched.

**Equity formula**: `equity = cash + market_value`, where `cash` is the free RUB
balance from `GetSandboxPositions` and `market_value` marks every `GetSandboxPortfolio`
holding at `current_price` (falling back to `average_price`; an unpriced holding is
excluded and logged rather than guessed). Realized PnL is **not** added: it is already
inside the broker's cash balance, so adding it again would double count it and
understate the drawdown the gate is measured on. `realized_pnl_rub` (sum of
`live_positions.pnl_rub` over closed rows) and `unrealized_pnl_rub`
(`market_value - cost_basis`) are persisted as separate observability columns.

**Price marking (Issue #191)**: a broker price is only usable when it parses to a
**finite number strictly greater than zero** (`LiveExecutor._mark_price`). A
`current_price` of `0`, a negative or a non-finite value falls back to `average_price`
and the ticker is recorded in `stale_priced_holding_tickers`; if `average_price` is
unusable too, the holding is left out of `market_value` and its ticker lands in
`unpriced_holding_tickers` (one `live equity: no usable price for holding ...` warning
per holding per snapshot). Both degradations are counted in `holdings_unpriced_total` /
`holdings_stale_priced_total` (cumulative for the life of the process), alerted in
Telegram (`unpriced_holding:<TICKER>` and `holding_marked_at_average:<TICKER>`, both
critical and deduplicated, plus `unpriced_holding_resolved:<TICKER>` once the broker
quotes the holding again), carried into the `risk_breach` alert details and exposed
through `get_metrics()` next to `equity_last_cash_rub` / `equity_last_market_value_rub`.
The ticker lists describe the latest snapshot only and reset to empty on the next fully
readable one, so a counter that keeps growing means the broker feed is still bad. This is
what stops a freshly opened position from being marked at zero and manufacturing a
drawdown the account never had.

**Daily drawdown basis**: `session_key` is the MSK calendar day of the snapshot.
`peak_equity_rub` is the peak *within that session_key*, which is what makes
`max_daily_loss_pct` a daily limit; a lifetime peak would turn it into an all-time
drawdown limit that could block entries forever. `peak_equity_all_time_rub` is kept
in a separate column for monitoring. `drawdown_pct = (peak - equity) / peak * 100`.

**Gates** (all in `LiveExecutor.process_signal`, in this order):
1. `risk_breach` - the daily drawdown gate. Evaluated from the in-memory snapshot,
   so it costs no broker call and no query, and runs before the order book and
   sizing. A breached account blocks **new entries only**: existing positions keep
   their broker stops and `monitor_positions()` keeps re-arming any that are
   missing. There is **no forced flatten** (Product Owner decision, 2026-09-18).
2. `max_open_positions` - pre-existing gate, unchanged; `LIVE_TRADING` stays its
   single source of truth (#176 only added the `MAX_OPEN_POSITIONS` env override).
3. `position_size_limit` - the absolute notional cap
   `size_lots * lot_size * entry_price > max_position_size`, applied after
   `calculate_position_size` and before `execute_order`. Measured on the executable
   order rather than the sizer's pre-rounding budget, so the `min_lot` branch (which
   forces `size_lots` to 1) cannot slip an oversized position through.

**Breach recovery**: automatic on the next MSK trading day (a new `session_key`
starts a new peak), or manual by setting `trading.app_settings.live_risk_breach_reset`
to `true`. The flag is self-consuming - the executor writes `false` back - so a stale
`true` cannot disarm a later breach. `initialize()` re-latches a breach that is
already true for today's `session_key`, so restarting the process cannot be used to
bypass the gate. A latched breach stays `true` for the rest of the day even if equity
recovers, so the persisted flag and the gate never disagree within one session.

**Configuration** (`trading_config.py`, section `LIVE_RISK` + `get_live_risk_config()`):
- `max_daily_loss_pct`: default `2.0`, env `MAX_DAILY_LOSS_PCT`, range `(0, 100]`.
- `max_position_size`: default `100000` RUB, env `MAX_POSITION_SIZE`, range `(0, 1e12]`.
- `equity_snapshot_enabled`: default `true`, env `LIVE_EQUITY_SNAPSHOT`. Off disables
  both the snapshot and the drawdown gate - the dry-run switch for the risk contour.
- `max_open_positions`: default `5` in `LIVE_TRADING`, env `MAX_OPEN_POSITIONS`, range `[1, 100]`.

An unparsable or out-of-range env value raises `ValueError` at read time, so a typo in
`.env` fails fast instead of silently disabling a risk gate. `LiveExecutor._validate_config`
re-checks the same bounds, so an in-memory config override cannot smuggle in a value
that `.env` would have rejected. These limits deliberately do **not** live in
`config_manager.RiskConfig`: that object is the paper risk policy read from
`config/settings.yaml` and consumed by `paper_trader.write_equity`.

**Database**: migration `20260927_001_live_equity.py` (`down_revision = 20260916_001`)
creates `trading.live_equity` with two indexes (`timestamp DESC` and
`(session_key, timestamp DESC)`) and seeds `live_risk_breach_reset` in
`trading.app_settings`. `live_schema.ensure_live_equity_schema()` is the idempotent
runtime form of the same DDL, applied from `initialize()`, so a standalone executor
start converges on an unmigrated database. `timestamp` is `TIMESTAMP WITHOUT TIME ZONE`
holding naive MSK, matching `paper_equity.timestamp` and `live_positions.signal_ts`.

**Resilience**: `_write_live_equity()` runs once per cycle from `run()`, *before*
`monitor_positions()` and `process_latest_bars()`, so every gate reads a fresh
drawdown. It is entirely wrapped in `try/except`: a snapshot failure increments
`equity_snapshot_errors_total`, logs a warning and never propagates into the loop or
touches `_consecutive_errors`. Its two broker calls use the new `equity` rate-limit
priority with `blocking=False` and the same token reserve as `entry`, so a snapshot is
deferred (counted in `equity_snapshot_skipped_total`) rather than taking a token that
stop arming or an amend needs. Before the first snapshot the drawdown gate is
**fail-open** and logs a warning once - blocking every entry on a rate-limit hiccup
would be worse than trading one cycle without a fresh reading.

**Alerting**: a breach transition logs `logger.critical` once (not every cycle),
**flushes the metrics snapshot immediately** (#191 - the alert must not stay invisible
for up to 60 s while the entry gate is already rejecting) and exposes counters through
`get_metrics()`: `risk_breach_active`, `risk_breach_total`,
`risk_breach_resets_total`, `risk_gate_rejections_total`, `position_size_rejections_total`,
`equity_snapshots_total`, `equity_snapshot_errors_total`, `equity_snapshot_skipped_total`,
`holdings_unpriced_total`, `holdings_stale_priced_total`,
`unpriced_holding_tickers`, `stale_priced_holding_tickers`,
`last_equity_rub`, `last_drawdown_pct`, `last_peak_equity_rub`, `last_equity_session_key`,
`equity_last_cash_rub`, `equity_last_market_value_rub`
and the effective limits. The `*_total` holding counters are cumulative for the life of
the process, the ticker lists describe the most recent snapshot only. The `risk_breach`
Telegram message carries the same measurement context (cash, market value, the unpriced
tickers and the tickers marked at their average price) and the drawdown-gate rejection
payload adds an `unpriced_holdings` detail, which is what lets an operator tell a real
drawdown from a marking failure without reading the container log.
`GET /api/live-trading/metrics` re-exposes the split as `risk.last_cash_rub` /
`risk.last_market_value_rub` next to `risk.unpriced_holding_tickers` /
`risk.stale_priced_holding_tickers`. Telegram delivery for `risk_breach`
is implemented by task E (#177): see §23.

**Testing**: `backend/tests/test_live_equity_risk_gates.py` (83 tests) covers the schema
contract and migration chain, config defaults / env overrides / range validation, the
equity formula and its no-double-count property, price marking and the unpriced /
stale-price bookkeeping, the 28.09 phantom-breach sequence, the daily versus all-time
peak, breach latching and recovery (auto, manual, restart), both entry gates,
rate-limit deferral, failure containment, `get_metrics()` and the three endpoints.

## 44. Operating the live equity risk gates (Issue #176)

### Why

Before #176 the live contour had no equity accounting at all: `trading.live_equity` did
not exist, the risk limits in `config/settings.yaml` were consumed only by the paper
trader, and nothing defined what happens when the account draws down. #176 adds the
snapshot, the gates and the recovery path. Architecture details: `project-context.md` §22.

### What changed

- `backend/alembic/versions/20260927_001_live_equity.py` - creates `trading.live_equity`
  (16 columns, 2 indexes) and seeds `live_risk_breach_reset` in `trading.app_settings`.
- `backend/app/analytics/trading_config.py` - new `LIVE_RISK` section,
  `get_live_risk_config()`, `get_live_risk_bounds()` and the env overrides
  `MAX_DAILY_LOSS_PCT` / `MAX_POSITION_SIZE` / `MAX_OPEN_POSITIONS` / `LIVE_EQUITY_SNAPSHOT`.
  `get_live_trading_config()` now resolves the `MAX_OPEN_POSITIONS` override.
- `backend/app/analytics/live_schema.py` - `ensure_live_equity_schema()` /
  `ensure_live_runtime_schema()`, `REQUIRED_LIVE_EQUITY_COLUMNS`,
  `LIVE_EQUITY_SCHEMA_STATEMENTS`, `RISK_BREACH_RESET_KEY`. Kept in a **separate**
  statement tuple: `test_live_schema.py` asserts that `LIVE_SCHEMA_STATEMENTS` contains
  no `DROP TABLE` / `DROP COLUMN`, and the equity downgrade does drop.
- `backend/app/analytics/live_executor.py` - `_write_live_equity()`, `_compute_live_equity()`,
  `_daily_peak_equity()`, `_all_time_peak_equity()`, `_realized_pnl_rub()`,
  `_risk_gate()`, `_activate_risk_breach()`, `_clear_risk_breach()`,
  `_restore_risk_breach_state()`, `_refresh_risk_breach_reset()`, `_account_id()`;
  the `equity` rate-limit priority; the two gates in `process_signal()`; the snapshot call
  in `run()`; risk fields in `get_metrics()`.
- `backend/app/api/live_trading_jobs.py` - `/api/live-trading/equity/current`,
  `/latest` (alias) and `/history`.
- `.env.example` - the four new variables.

### Behaviour an operator must know

- The gate is **daily**, not lifetime. `peak_equity_rub` resets with `session_key`
  (MSK calendar day), so a breach releases itself on the next trading day.
- A breach blocks **new entries only**. Open positions keep their broker stops and the
  monitor loop keeps re-arming missing ones. There is no forced flatten.
- A breach stays latched for the rest of the MSK day even if equity fully recovers.
- Restarting the executor does **not** clear a breach: `initialize()` re-latches it from
  the newest `live_equity` row of today's `session_key`.
- Before the first snapshot of a process the drawdown gate is **fail-open** (logged once
  as a warning). Entries are not blocked by a missing or deferred snapshot.
- Turning `LIVE_EQUITY_SNAPSHOT=off` disables the snapshot **and** the drawdown gate.
  The notional cap and `max_open_positions` keep working - they do not depend on equity.
- A failing snapshot never stops trading: it increments `equity_snapshot_errors_total`
  and logs a warning. Watch that counter, not the loop.

### Commands

```bash
# Apply the migration (the backend image does not bundle alembic/ - see limitations)
cd backend && python -m alembic upgrade head
python -m alembic current      # expect 20260927_001 (head)
python -m alembic downgrade -1 # revert; drops trading.live_equity
python -m alembic upgrade head

# Verify the table and the seeded reset switch
psql -c "SELECT count(*) FROM trading.live_equity;"
psql -c "SELECT key, value FROM trading.app_settings ORDER BY key;"

# Latest snapshot and the effective limits
curl -s http://localhost:8000/api/live-trading/equity/current | python -m json.tool
curl -s "http://localhost:8000/api/live-trading/equity/history?limit=50"

# Manually clear an active breach (self-consuming: the executor writes false back)
psql -c "UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() \
         WHERE key='live_risk_breach_reset';"

# Override the limits for one run without editing code
MAX_DAILY_LOSS_PCT=1.5 MAX_POSITION_SIZE=50000 MAX_OPEN_POSITIONS=3 \
  python -m app.analytics.live_executor

# Tests (83)
cd backend && python -m pytest tests/test_live_equity_risk_gates.py -q
```

### Phantom drawdown triage (Issue #191)

On 2026-09-28 the executor latched a `risk_breach` the account never had: a freshly
opened position came back from `GetSandboxPortfolio` with `current_price = 0`,
`market_value` silently lost that holding, equity dropped and the daily drawdown gate
fired. #191 makes the marking explicit instead of silent.

- A price is usable only when it parses to a **finite, strictly positive** number
  (`LiveExecutor._mark_price`). An unusable `current_price` (`0` / negative / `nan`)
  falls back to `average_price` and the ticker lands in `stale_priced_holding_tickers`;
  with no usable price at all the holding stays out of `market_value` and its ticker
  lands in `unpriced_holding_tickers`.
- Both lists are published per snapshot: Telegram (`unpriced_holding:<TICKER>` critical,
  `unpriced_holding_resolved:<TICKER>` once the broker quotes it again,
  `holding_marked_at_average:<TICKER>` - both `critical` + `dedupe=True`; the resolved
  message is not debounced because the executor sends it exactly once per ticker),
  `get_metrics()`
  (`unpriced_holding_tickers`, `stale_priced_holding_tickers`, the cumulative
  `holdings_unpriced_total` / `holdings_stale_priced_total`, `equity_last_cash_rub`,
  `equity_last_market_value_rub`) and the `risk` block of
  `GET /api/live-trading/metrics` (`unpriced_holding_tickers`,
  `stale_priced_holding_tickers`, `holdings_unpriced_total`,
  `holdings_stale_priced_total`, `last_cash_rub`, `last_market_value_rub`). The ticker
  lists describe the latest snapshot only and empty themselves on the next fully readable
  one; the `*_total` counters are cumulative for the life of the process, so a counter
  that keeps growing means the feed is still bad.
- The `risk_breach` alert carries that measurement next to the percentage (`Кэш`,
  `Стоимость позиций`, `Без цены`, `По средней цене`), and the drawdown-gate rejection
  payload adds an `unpriced_holdings` detail, so a blocked entry states whether the
  drawdown it acted on was measured on a portfolio the broker could price.
- A breach latch now **flushes the metrics snapshot immediately**
  (`_flush_metrics(force=True)`), so `risk_breach_active` in `/metrics` is no longer up
  to `LIVE_ALERTING.metrics_flush_seconds` (300 by default) behind a gate that is already
  rejecting entries.

**Triage order when `risk_breach` fires:**

1. Read the alert (or `/api/live-trading/metrics`). A non-empty `unpriced_holding_tickers`
   or `stale_priced_holding_tickers` means the drawdown was measured on an incomplete
   portfolio - handle it as a broker-feed incident first.
2. Cross-check the split: `last_cash_rub + last_market_value_rub` must equal
   `last_equity_rub`. A collapsing `market_value` against an unchanged `cash` is marking,
   not losses.
3. Confirm against history and the log:
   `curl -s "http://localhost:8000/api/live-trading/equity/history?limit=50"` and the
   `live equity: N holding(s) ...` warnings in `docker compose logs backend`.
4. If the numbers are real, leave the breach latched for the MSK day. If it is a feed
   artefact, wait for the next clean snapshot (the counters reset by themselves) and then
   clear the latch through `live_risk_breach_reset`.

### Known limitations

- **`alembic` is not in the backend image.** `backend/Dockerfile` copies only `app/` and
  `tests/`, so `docker compose exec backend alembic ...` fails with
  `No 'script_location' key found`. Run migrations from the host, or `docker compose cp`
  `backend/alembic.ini` and `backend/alembic` into `/app` first. Making
  `alembic upgrade head` a deploy step is task F (#178).
- **`app/core/config.py:get_app_database_url()` reads only `POSTGRES_PASSWORD`**, while
  `docker-compose.yml` passes `PSTGRS_PWD`. Inside the container alembic therefore tries
  the default password `app` and fails; the failure surfaces as a misleading
  `UnicodeDecodeError` from psycopg2 because the server's Russian error message is not
  UTF-8. Workaround used for verification:
  `docker compose exec -T backend sh -c 'POSTGRES_PASSWORD="$PSTGRS_PWD" alembic upgrade head'`.
  `config_manager.load_settings()` already accepts both names - aligning `app/core/config.py`
  with it belongs to #178.
- `strategy_name` is `NULL` on snapshots written outside a full `initialize()` run (the
  executor only learns the strategy there).
- The drawdown gate reads the in-memory snapshot of the current process. If the executor
  is down, no new snapshots are written and the gate cannot tighten - it does not
  retro-block on stale data beyond the restore described above.
- Telegram alerting for `risk_breach` is delivered by task E (#177):
  `LiveExecutor._notify("risk_breach", ..., critical=True)` fires on top of the same
  `logger.critical` and `get_metrics()` counters. It needs no debounce - the latch fires
  once per session (decision D2). See §45.
