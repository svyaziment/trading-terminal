# Operating the T-Bank Sandbox Client

> **Source:** handover.md sections 13, 46
> **Last refreshed:** 2026-10-04, task-346

## 13. Operating the T-Bank Sandbox Client

- Entry point: `app.broker.tinkoff_sandbox.TinkoffSandboxClient`. Keep all broker order execution behind this class; downstream executors must not instantiate or call the production `orders` service.
- Required environment: dedicated sandbox token `TINVEST_SANDBOX`. Optional `TINVEST_SANDBOX_ACC` pins the sandbox account; otherwise the first open account is discovered. The client deliberately never falls back to market-data credentials `TINVEST_TOKEN` / `TINVEST_ACC`. Account discovery never opens or funds an account.
- Read-only smoke check:
  `cd backend && python -c "from app.broker.tinkoff_sandbox import TinkoffSandboxClient; print(TinkoffSandboxClient().check_balance())"`
- Market order: pass `instrument_id`, a positive integer `quantity` in lots, and optionally `direction` (`buy`/`sell`). Do not pass `price`.
- Limit order: pass the same fields plus `order_type="limit"` and a positive `price`. Use the instrument UID/FIGI accepted by T-Bank as `instrument_id`.
- Cancellation requires the broker `order_id` returned by `execute_order`.
- Retry policy comes only from `SANDBOX_TRADING` in `trading_config.py`. Do not add independent retry loops around `execute_order`: the client already retries transient gRPC failures with the same idempotency key.
- The client does not open a sandbox account or deposit the epic's 50,000 RUB automatically. Provisioning/funding is an explicit operator step. Never print tokens or commit `.env`.
- Unit test: `cd backend && python -m pytest -q tests/test_tinkoff_sandbox.py`.

## 46. The real T-Bank contour, deploy migrations and the global kill switch (Issue #178)

**What changed.** The live contour is no longer sandbox-only by construction: the
executor picks its broker client through a factory, migrations are an explicit
deploy step, and the operator got a global entry emergency stop. Russian
original: `handover.ru.md` §46; architecture: `project-context.md` §24.

### 46.1 Three credential pairs (strictly separated)

| Purpose | Token | Account | Read by |
|---|---|---|---|
| Market data (candles, orderbook) | `TINVEST_TOKEN` | `TINVEST_ACC` | `data_loader`, `online_data` |
| Sandbox execution | `TINVEST_SANDBOX` | `TINVEST_SANDBOX_ACC` | `TinkoffSandboxClient` |
| REAL account execution | `TINVEST_LIVE_TOKEN` | `TINVEST_LIVE_ACC` | `TinkoffLiveClient` |

Cross-pair fallback is refused in code: `TinkoffLiveClient` raises
`LiveConfigurationError` when `TINVEST_LIVE_TOKEN` is empty **or equals**
`TINVEST_TOKEN` (the "filled the wrong variable" case). Since Issue #192 the second
refusal has an explicit opt-out for a deployment that deliberately runs ONE
physical token for market data and for the real account:
`ALLOW_LIVE_TOKEN_REUSE=true` (`false` by default, `False` in code, parsed strictly
like `ALLOW_REAL_TRADING`). The client then builds and logs a WARNING, because
rotating that token changes both contours at once. The opt-in never introduces a
fallback - each client still reads its own variable only. The token is never
logged; the account id is logged masked (`***1234`). `TINVEST_LIVE_ACC` may stay
empty - the first open account from `users.get_accounts()` is then used, with a
WARNING recommending an explicit id when several accounts are open.

### 46.2 How the contour is selected

The single source of truth is `SANDBOX_TRADING.allow_real_trading`
(`backend/app/analytics/trading_config.py`), `False` in code (an Epic #172 red
line). The only override is the `ALLOW_REAL_TRADING` env variable: the accepted
words are `1/true/yes/on` and `0/false/no/off`; anything else raises `ValueError`
at startup, so a typo like `ture` can never silently pick a contour.

`app/broker/client_factory.create_execution_client()`:

- gate closed -> `TinkoffSandboxClient`, INFO log `Using TinkoffSandboxClient: T-Bank sandbox contour`;
- gate open -> `TinkoffLiveClient`, **WARNING** log `Using TinkoffLiveClient: REAL T-Bank account contour`.

The selected contour is visible to the operator in three places: `broker_contour`
in the metrics snapshot (`source` section of `GET /api/live-trading/metrics`), the
titles of the `live_start` / `live_entry` / `live_exit` alerts ("sandbox" / "real
account" in Russian) and the "broker contour" field of the start alert. With
`ALLOW_REAL_TRADING=true` the sandbox client refuses to be constructed at all, so
the two contours cannot be mixed inside one process.

### 46.3 Runbook: switching to the real account

The order is mandatory - each step is verified before the next one.

1. **The sandbox is green.** A `LiveExecutor` run with no protection failures
   (`protection_failed_total == 0`, `invariant_violations_total == 0`) and a
   preflight reporting `ok=true`:
   ```bash
   docker compose exec -T backend python -m app.analytics.live_executor_preflight
   ```
2. **Migrations applied.** `alembic current` reports `20260928_001 (head)`:
   ```bash
   docker compose run --rm migrate alembic current
   ```
3. **Real-contour credentials** in `.env` (the file itself is not in git):
   `TINVEST_LIVE_TOKEN`, `TINVEST_LIVE_ACC` (explicit is recommended) and
   `ALLOW_REAL_TRADING=false` until both exist.
4. **Credential check without trading.** A preflight run that expects the real
   contour:
   ```bash
   ALLOW_REAL_TRADING=true PREFLIGHT_EXPECT_CONTOUR=real \
     docker compose run --rm -e ALLOW_REAL_TRADING -e PREFLIGHT_EXPECT_CONTOUR \
     -e TINVEST_LIVE_TOKEN -e TINVEST_LIVE_ACC migrate \
     python -m app.analytics.live_executor_preflight
   ```
   Expect `contour=real`, `contour_matches_expectation=true`,
   `sandbox_free_rub > 0` (that is the REAL account's free cash) and
   `live_positions_schema=true`.
5. **Risk limits sized for real capital.** `MAX_POSITION_SIZE`,
   `MAX_DAILY_LOSS_PCT`, `MAX_OPEN_POSITIONS` in `.env`; the effective values are
   published by `GET /api/live-trading/equity/latest` -> `risk.limits`.
6. **Enable.** `ALLOW_REAL_TRADING=true` in `.env`, then rebuild and restart:
   ```bash
   docker compose up -d --build backend
   START_LIVE_EXECUTOR=1 ./start_processes.sh
   ```
7. **Watch the first cycle.** The executor log must contain the WARNING
   `Using TinkoffLiveClient`, the `live_start` alert must say the real contour,
   and `GET /api/live-trading/metrics` must report
   `source.broker_contour == "real"`.
8. **Watch the first fill.** After the first `live_entry`, verify that
   `protection.stops_armed_total` grows and `positions.unprotected_total == 0`.

Rolling the enablement back: `ALLOW_REAL_TRADING=false` ->
`docker compose up -d --build backend`. Open positions on the real account are
**not** closed automatically (`close_positions_on_shutdown=false`): their broker
stops stay armed and are handled either by the restarted executor or manually.


### 46.4 The global kill switch

**What it is.** One boolean key, `trading.app_settings.live_kill_switch`
(migration `20260928_001`). `true` - the executor rejects **every new entry**
with the reason `kill_switch`; `false` - entries are allowed again.

**What it deliberately does NOT do** (Epic #172 red lines):

- it does not close open positions (no auto-flatten);
- it does not cancel or disarm broker stops - position protection survives;
- it does not touch the paper contour or the `trailing_kill_switch` (a separate
  lever: that one pauses stop ratcheting, not entries).

**How to operate it.**

```bash
# Engage (stop new entries)
curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true, "reason": "abnormal volatility"}'

# Release
curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
  -H 'Content-Type: application/json' -d '{"enabled": false}'

# The same by SQL - the endpoint is not the only door
docker compose exec -T backend python -c "from app.db.db_manager import DBManager; \
DBManager().execute(\"UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() WHERE key='live_kill_switch'\")"
```

The response carries `ok`/`confirmed`, which is a **read-back confirmation**: when
the row cannot be read after the write, `ok=false` (an unconfirmed emergency stop
is never reported as a success). An unwritable `trading.app_settings` answers
`503` naming the migration. `reason` (max 200 characters) goes to the container
audit log and to the response; it is not stored.

**Latency.** The executor re-reads the key every cycle
(`LIVE_TRADING.check_interval_seconds`, 30 s by default) - no restart needed.

**Fail-safe (decision D2).** A missing row, a `NULL` value or a database error is
treated as **ON**: an executor that cannot read its own emergency stop does not
open entries. The in-memory default `LIVE_TRADING['live_kill_switch']` is `true`
as well, and `initialize()` reads the stored value before the first cycle, so a
restart on a migrated database is not a "transition" and sends no alert.

**Alerts.** Transitions are published to Telegram under the existing keys
`kill_switch_on` / `kill_switch_off` (critical only for the ON direction), titled
"Global kill switch ENGAGED/RELEASED" (in Russian), with the value provenance in
the source field (`app_settings`, `app_settings:missing_key`,
`app_settings:null_value`, `db_error:<type>`). Stream order: the trailing switch
first (as before #178), then the global one. The metrics snapshot is flushed
right after a transition (decision D5).

**Where the state is visible.**

```bash
curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool
```

- `global_kill_switch.active` - what the executor will apply (fail-safe when the row is absent);
- `global_kill_switch.found` / `.reason` - whether the value came from the row or from the
  fail-safe rule (`missing_row` / `unreadable_row`);
- `global_kill_switch.live_active` vs `.snapshot_active` - the DB row against the last snapshot
  (a difference is published, not smoothed over);
- `global_kill_switch.rejections_total` - how many signals were rejected;
- `kill_switch.*` - the trailing switch, reported independently;
- `state` = `kill_switch` when either lever is engaged.


### 46.5 Deploy: migrations as an explicit step

```bash
docker compose up -d --build backend   # builds, migrates, then serves
docker compose run --rm migrate        # migrations only
docker compose logs migrate --tail 50  # what was applied
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic history --verbose
```

- The `migrate` service is one-shot (`command: ["alembic","upgrade","head"]`,
  `restart: "no"`) and `backend.depends_on.migrate.condition =
  service_completed_successfully`: a failing migration stops the deploy instead
  of surfacing in production (`assert_live_schema` would abort the executor
  anyway).
- The image carries `alembic.ini` and `alembic/` (`COPY` in
  `backend/Dockerfile`) - before #178 it did not.
- `migrate` and `backend` share ONE environment block (the `x-backend-env` YAML
  anchor), so the migration DSN and the application DSN cannot drift apart.
- `alembic/env.py` resolves the URL through
  `app.core.config.get_app_database_url()`. Since #178 that function reads the
  password as `POSTGRES_PASSWORD` -> `PSTGRS_PWD` -> `app`; before, `PSTGRS_PWD`
  (the name compose actually passes) was ignored and in-container migrations
  failed authentication.
- There is no auto-migration in application code (decision D4): the runtime DDL
  `ensure_live_runtime_schema()` remains the safety net for a standalone start
  and converges idempotently to the same shape.

### 46.6 Rollback

```bash
docker compose run --rm migrate alembic downgrade -1     # 20260928_001 -> 20260927_001
docker compose run --rm migrate alembic downgrade 20260927_001
```

Rolling back `20260928_001` deletes **only** the `live_kill_switch` row
(`DELETE FROM trading.app_settings WHERE key='live_kill_switch'`); no table or
data is touched. Important: a deleted row reads as ON (fail-safe), so rolling the
migration back **blocks entries** rather than re-enabling them. Rolling the whole
feature back means the previous image plus no `ALLOW_REAL_TRADING` in `.env`.

### 46.7 Diagnostics

| Symptom | Cause | Action |
|---|---|---|
| `LiveConfigurationError: TINVEST_LIVE_TOKEN is empty` | gate open, no token | fill `.env` or set `ALLOW_REAL_TRADING=false` |
| `... must not reuse the market-data TINVEST_TOKEN` | one token in two variables | deliberate setup: `ALLOW_LIVE_TOKEN_REUSE=true`; otherwise check which token T-Bank issued |
| `LiveAPIError: T-Bank live <method> failed` with `TypeError ... unexpected keyword argument` in the log | the call shape drifted from the installed SDK | `192-callshape-check.py` (Issue #192); `t-tech-investments` is pinned to `1.51.0` |
| `migrate` exits 1 with `No module named 'psycopg'` | SQLAlchemy 2.1 made psycopg3 the default driver of a bare `postgresql://` URL | keep `sqlalchemy<2.1` pinned (Issue #192) or install `psycopg[binary]` |
| `Refusing to build a real-money client` | `ALLOW_REAL_TRADING` never reached the container | `docker compose exec backend env \| grep ALLOW_REAL` |
| No entries, `reason=kill_switch`, `found=false` | the `live_kill_switch` row is missing | `docker compose run --rm migrate` |
| `alembic` not found in the container | stale image | `docker compose up -d --build backend` |
| Migration fails on auth | the password did not arrive | `PSTGRS_PWD` / `POSTGRES_PASSWORD` in `.env` |

### 46.8 Known limitations

- The real contour's `GetStopOrders` has no date filter
  (`GetStopOrdersRequest` = `account_id` + `status`), so `from_date`/`to_date` of
  `TinkoffLiveClient.get_stop_orders()` are accepted for signature parity and
  ignored; instrument filtering is client-side (uid / FIGI / ticker).
- The idempotency key of a real order is passed as `idempotence_id` (`order_id`
  in the real API is the exchange order number).
- Real account discovery picks the first open account; with several open accounts
  (brokerage + IIS) pin `TINVEST_LIVE_ACC` explicitly.
- `live_kill_switch` blocks **entries only**. Exits, trailing, OCO monitoring and
  fill reconciliation keep running - by design: an emergency stop must never
  leave a position unprotected.
- The kill-switch endpoint is not authenticated (like the rest of the terminal
  API): it assumes a local/trusted network.
- The preflight check `real_trading_disabled` was replaced by
  `contour_matches_expectation` + `PREFLIGHT_EXPECT_CONTOUR`; older checklists
  referencing the previous key name must be updated.

### 46.9 Tests

```bash
cd backend
python -m pytest tests/test_tinkoff_live.py -q        # real client, gate, factory (51)
python -m pytest tests/test_live_kill_switch.py -q    # migration, gate, fail-safe, API (41)
python -m pytest tests/test_deploy_migrations.py -q   # alembic chain, Dockerfile, compose, DSN (15)
```


### 46.10 Read-only verification of the real contour (Issue #192)

Two diagnostics live with the working artifacts in
`reports/190-production-trading-infrastructure/192-g1-production-client-verify/`
(they are not part of the image and never enter the trading path):

- **`192-contract-check.py`** - the safety contract of the broker layer, verified
  without credentials and without a single network call. Checks: the global gate
  stays closed, `create_execution_client()` returns the sandbox client by default,
  a forced real construction fails closed, live/sandbox method and keyword
  parity, mutating vs read-only method classification, live error types inherit
  the sandbox ones. Verdict `CONTRACT_OK`, exit `0`.
- **`192-live-smoke.py`** - read-only smoke of the real contour. It never flips
  `ALLOW_REAL_TRADING`: the client is built through the diagnostic constructor
  argument `allow_real_trading=True`, then every mutating method
  (`execute_order`, `cancel_order`, `post_stop_order`, `cancel_stop_order`) is
  shadowed on the instance by a raising guard. The guard installation is verified
  through a marker attribute, **not** by calling the method - a call-probe on the
  real contour would itself be a mutating request. Only read-only APIs are
  called: `users.get_accounts`, `check_balance`, `get_positions`, `get_orders`,
  `get_stop_orders(status="active")`, `get_operations(state="executed", 7 days)`.
  `--self-test` runs the same logic against an in-process fake client (no
  credentials, no network) and additionally call-probes the guards there.
  Exit codes: `0` green / self-test green, `1` failed, `3` blocked because
  `TINVEST_LIVE_TOKEN` is absent.

Run order (from the issue folder; artifacts are produced inside the container and
copied back with `docker compose cp`, which keeps them UTF-8):

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

Masking rules of the artifacts: tokens are reported only as presence and length
(`set(len=88)` / `(empty)` / `<not-set>`), never as a prefix; account ids are
reduced to the last four characters (`***7890`).

Status 2026-09-29: `contract-check.txt` = 9/9 green, `smoke-self-test.txt` =
`SELF_TEST_OK` (9 read-only steps, `orders_placed=0`), the authenticated run is
`BLOCKED_NO_CREDENTIALS` (exit `3`) because `TINVEST_LIVE_TOKEN` is not present
in this environment. The preflight probe (`192-preflight-check.py` with
`PREFLIGHT_EXPECT_CONTOUR=real`) confirms the expected fail-closed behaviour:
`contour_now=sandbox`, `sandbox_gate=true`, `real_gate=false`,
`real_expectation_passes=false`, verdict `FAIL_CLOSED_OK` (exit `0`) - the
production migration path of §46.3 step 3 cannot start while the gate is closed
and no live token exists. Nothing was ever sent to the exchange: the real contour
was constructed only to read balances, positions and history.


### 46.11 Stopping the stream without flatten-all: three levers, runbook, drill (Issue #193)

**Why this section exists.** Epic #190 keeps the PO decision: **no flatten-all**.
Stopping the stream must never sell anything. Three levers stop the stream, they
do different things, and on real money the difference between "a managed stop"
and "a naked position" is exactly which lever was pulled. Nothing here is new
code - the mechanisms shipped in #174 (SIGTERM/shutdown), #151 (trailing switch)
and #178 (global switch). This section is the matrix, the operator runbook and
the drill that proves all three.

**The matrix of the three levers.**

| | Lever 1: global kill switch | Lever 2: trailing kill switch | Lever 3: SIGTERM / SIGINT |
|---|---|---|---|
| Mechanism | `trading.app_settings.live_kill_switch` (#178, migration `20260928_001`) | `trading.app_settings.trailing_kill_switch` (#151) | signal to the process: `stop_processes.sh`, `docker compose stop backend`, Ctrl+C |
| How to engage | `POST /api/live-trading/kill-switch {"enabled": true, "reason": "..."}` or SQL | **SQL only - there is no API endpoint** (§41) | `./stop_processes.sh` (SIGTERM to `LiveExecutor`) |
| Latency | ≤ `check_interval_seconds` (30 s): re-read every cycle, no restart | ≤ 30 s: re-read every cycle, no restart | immediate: the handler sets `shutdown_requested`, cleanup runs in `shutdown()` |
| New entries | **blocked** - skip reason `kill_switch`, counted in `kill_switch_rejections_total`, evaluated before the session window / order book / sizing / any broker call | allowed - this lever does not touch entries at all | none: the process is gone |
| Trailing ratchet | keeps working | **frozen**: no arming, no ratchet, no broker amend | stops with the process |
| Open positions | untouched | untouched | untouched - **no flatten** (`close_positions_on_shutdown=false`) |
| Broker stops | stay armed | stay armed | stay armed; only **pending entry orders** are cancelled and their rows marked `cancelled` (reason `shutdown`) |
| Fail-safe | missing row / `NULL` / DB error → **ON** (`app_settings:missing_key`, `app_settings:null_value`, `db_error:<type>`) | missing row → **False** (fail-open, historical default); DB error → **True** (fail-safe) | n/a |
| Restart needed | no | no | `./start_processes.sh` with `START_LIVE_EXECUTOR=1`; all state is restored from the DB |
| Where to verify | `global_kill_switch.*` in `GET /api/live-trading/metrics`; audit line `Global live kill switch set to ON (confirmed=... reason=...)`; Telegram `kill_switch_on` | `kill_switch.*` in `/metrics`; Telegram `kill_switch_on`; §41 | log line `Position <id> left protected with broker_stop_id=...`; `positions.protected_total` / `unprotected_total` in `/metrics` |

Only lever 3 cancels anything, and only pending entries. Levers 1-2 cancel
nothing: they change what the loop is allowed to do next, and both are read again
on every cycle. `close_positions_on_shutdown=true` is the only setting that makes
shutdown flatten - the project does not ship it and Epic #190 forbids it.

**Runbook A - planned stop of the stream, positions stay open.**

1. Block new entries:
   ```bash
   curl -s -X POST http://localhost:8000/api/live-trading/kill-switch \
     -H 'Content-Type: application/json' \
     -d '{"enabled": true, "reason": "planned stop 2026-09-30"}'
   ```
   The answer must carry `"ok": true, "confirmed": true` - `confirmed` is the
   read-back of the row. `ok=false` means the write did not stick: do not continue.
2. Verify: `curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool`
   → `global_kill_switch.active=true`, `state="kill_switch"`; the container log
   carries `Global live kill switch set to ON (confirmed=True reason=...)`;
   Telegram got `kill_switch_on`.
3. Optional - freeze the ratchet as well (SQL only):
   ```sql
   UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now()
   WHERE key='trailing_kill_switch';
   ```
4. Wait one cycle (≤ 30 s) so the loop finishes the work it already started.
5. Stop the process: `./stop_processes.sh` (SIGTERM). Never `docker compose kill`
   and never `kill -9`: SIGKILL skips `shutdown()`, so resting pending entry
   orders stay at the broker and the final metrics snapshot is never written.
6. Verify that the protection survived:
   - log: one `Position <id> left protected with broker_stop_id=... broker_take_id=...`
     per open position;
   - `/api/live-trading/metrics` → `positions.open_total == positions.protected_total`,
     `unprotected_total = 0`, `unprotected_tickers = []`;
   - the rows in `trading.live_positions` are unchanged: `status='open'`, the same
     `broker_stop_id` / `broker_take_id`, the same `updated_at`;
   - the stops are alive at the broker - read-only `get_stop_orders(status='active')`
     (`193-shutdown-drill.py` does exactly this before and after the signal).
7. Only now close positions by hand in the broker application, if you want out.

**Runbook B - emergency: stop the entries, keep the process running.**

Use this when the contour misbehaves but the positions must stay protected and
managed (stops keep firing, the ratchet keeps working unless lever 2 is pulled):
step 1-2 of Runbook A, and nothing else. Release with
`{"enabled": false, "reason": "..."}` and verify `global_kill_switch.active=false`
plus the `kill_switch_off` alert. While the switch is ON every rejected signal is
counted (`global_kill_switch.rejections_total`) and logged with
`reason=kill_switch source=<provenance>`.

**Runbook C - the real contour: what to do before closing a position by hand.**

The PO decision is explicit: no flatten-all, manual closing through the broker
application. The order matters, because a running executor reconciles whatever it
sees at the broker:

1. Lever 1 ON (entries blocked) - so the executor cannot open a new position
   while you are working in the broker app.
2. Optional lever 2 ON - so stops do not move under your hands.
3. Lever 3: `./stop_processes.sh`. On the real contour the broker stop survives
   independently of the process: the position stays protected while nothing is
   being sent.
4. Verify as in Runbook A step 6. On the real contour `GetStopOrders` has no date
   filter (§46.8) - read the id list, do not guess.
5. Close the position manually in the broker application.
6. On the next start the executor reconciles: a vanished position is closed with
   the real fill from `GetOperations` and the leftover sibling order (stop or
   take) is cancelled. **Read the classification correctly**: with the stop still
   `ACTIVE` a manual close is recorded as `closed_take` when a take order existed
   and `closed_broker` otherwise (`_classify_exit_reason`); there is no "manual"
   reason in the frozen #173 status list, so trust `exit_price_actual` /
   `lots_executed` from the fill, not the word in `exit_reason`. An ambiguous case
   is logged as `exit_reason_ambiguous position_id=...`.
7. Release lever 1 only when the contour is back under the executor.

**Drill (Issue #193).** `193-shutdown-drill.py` lives with its artifacts in
`reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten/`
(not part of the image, never in the trading path). It exercises all three levers
and is **sandbox-only by construction**: when `create_execution_client()` resolves
to the real contour it stops with `DRILL_BLOCKED` (exit 3) before touching
anything. Every mutating broker method is shadowed by a counting guard verified by
marker (no call-probe), the only database write is the `live_kill_switch` row of
the round trip (restored in a `finally`, with a SQL fallback), and `_flush_metrics`
is stubbed during the shutdown phase so the drill cannot overwrite the
`live_executor_metrics` snapshot the panel serves.

Phases: `environment` (shipped policy + guards) → `kill_switch_roundtrip` →
`audit_line` → `entry_gate` (with the OFF negative control) → `fail_safe` →
`trailing_lever` → `stop_liveness_before` → `sigterm_shutdown` (a real SIGTERM to
the drill process, then `shutdown()` the way `run()` calls it) →
`stop_liveness_after` → `flatten_contrast` (`--self-test` only). Exit codes:
`0` `DRILL_OK` / `1` `DRILL_FAIL` / `3` `DRILL_BLOCKED`. The drill is also
fail-closed when a `pending` row exists: `shutdown()` would cancel that order at
the broker, which is a mutating action.

```bash
cd reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten
docker compose cp 193-shutdown-drill.py backend:/tmp/193-shutdown-drill.py

# hermetic: fakes only, no DB / API / broker - safe anywhere
docker compose exec -T backend python /tmp/193-shutdown-drill.py --self-test

# real sandbox drill
docker compose exec -T backend sh -c \
  'python /tmp/193-shutdown-drill.py --json /tmp/drill-193.json > /tmp/drill.txt 2>&1; echo exit=$?'
docker compose cp backend:/tmp/drill.txt      <issue-dir>/drill-sandbox.txt
docker compose cp backend:/tmp/drill-193.json <issue-dir>/drill-sandbox.json
```

Status 2026-09-30 (sandbox; `drill-sandbox.txt`, `drill-sandbox.json`):
`DRILL_OK`, exit `0`, **`mutating calls: 0`**, all four guards `blocked`. The
round trip was confirmed ON (`state="kill_switch"`, row `true`,
`positions.protected_total` unchanged) and restored the baseline `false`; the
entry gate returned `kill_switch` with the lever ON and `unknown_instrument` with
it OFF; the audit line carried `confirmed=True reason=drill-193 audit probe`; the
fail-safe matrix reproduced all three ON provenances; a real SIGTERM set
`shutdown_requested`, `shutdown()` left the open row (id 10, PLZL) byte-identical
(`updated_at` included) and logged `Position 10 left protected with
broker_stop_id=01a0df17-...`, and that stop was alive in `GetStopOrders` both
before and after. `--self-test` (`drill-selftest.txt`) is green too and adds the
two branches the sandbox could not show: a pending row is cancelled and marked
`cancelled`/`shutdown` while the open row is never written, and
`close_positions_on_shutdown=true` really does flatten - the configuration this
project does not ship.

**Finding of the drill (not fixed here - needs its own issue).** The sandbox
`GetStopOrders(active)` returned 4 stops while only 1 position is open: three stop
ids (`01a0d4fc-1e39...`, `01a0d4fc-7e55...`, `01a0def6-354f...`) are referenced by
no `live_positions` row (`drill-orphan-stops.txt`). Orphan sell stops are exactly
what `_reconcile_protection` refuses to re-arm around, and on the real contour
they would be naked orders. Cleanup and reconciliation of orphan stops is a
separate issue: #193 adds no code to the trading cycle.

**Regression tests behind the drill** (they are the second layer of evidence):
`test_live_executor.py::test_shutdown_leaves_position_protected_by_default`,
`::test_shutdown_cancels_all_pending_orders_without_flattening_by_default`,
`::test_shutdown_still_flattens_when_explicitly_requested`,
`::test_shutdown_cancels_broker_stop_through_the_stop_api`,
`::test_kill_switch_preserves_armed_positions`,
`::test_apply_trailing_returns_none_when_kill_switch_on`,
`test_live_kill_switch.py` (migration, gate, fail-safe, API),
`test_live_alerting.py::test_graceful_shutdown_persists_the_final_snapshot`.


### 46.12 Account-wide orphan stop sweep (Issue #199)

**Why.** The #193 drill found four ACTIVE stops at the broker with one open position: three ids referenced by no `live_positions` row (`drill-orphan-stops.txt`). The #175 OCO pass only knows the legs of the closes *this* process performed, so a stop orphaned by a crash between `PostStopOrder` and the DB write, by a failed close, by an amend that could not confirm its cancel, or by a manual intervention stays at the broker as a naked SELL order - it fires on the next dip and sells shares the account does not hold.

**What runs.** `_sweep_orphan_stops()` is the last pass of `_finish_monitor_cycle()`, so it also runs on the empty-book early return - exactly the moment every stop left in the account belongs to nobody. It is rate-limited by `orphan_stop_sweep_interval_seconds` (default 300, independent of `check_interval_seconds`: this is a reconciliation net, not a per-cycle pass) and reads `GetStopOrders(active)` plus, when the cycle did not read one, `GetPositions` - "the broker holds nothing" is evidence the sweep refuses to do without. An unreadable stop book or portfolio skips the pass (`orphan_stop_sweep_skipped reason=...`) and keeps the candidates of the previous one.

**What may be cancelled.** Only a stop that survives the whole fail-closed chain of `_orphan_stop_skip_reason()`: `not_active`, `not_a_sell_stop`, `outside_universe` (ticker / FIGI / instrument_uid of the configured live universe), `owned_by_oco_or_amend` (`_oco_checks` plus both sides of `_pending_stop_cancels`), `position_row_exists` (any `pending` / `open` row of the same instrument - the executor may be about to arm a stop for it), `broker_holding_exists` (the broker still holds the instrument, so removing its stop would be a human decision), `inside_grace_window` (`orphan_stop_grace_seconds`, default 900, counted from the moment *this* process armed the stop). Every kept stop logs `orphan_stop_kept stop_order_id=... reason=...` at DEBUG; an empty universe skips the pass instead of treating every stop as ours.

**What makes it act.** The same orphan must be reported by `orphan_stop_confirmations` (default 2) consecutive sweeps - the candidate table is replaced wholesale each pass, so a stop that disappears between sweeps starts over and a flapping one can never accumulate confirmations. More confirmed orphans than `orphan_stop_max_cancels` (default 3) trips the fail-closed branch: nothing is cancelled at all, `orphan_sweep_fail_closed_total` grows, `orphan_stop_sweep_fail_closed orphans=N max_cancels=M` is logged CRITICAL and a Telegram alert asks for a manual review, throttled by `orphan_stop_alert_interval_seconds` (default 3600) on top of the global debounce. `orphan_stop_max_cancels=0` is the "watch only" mode. A cancel the broker rejects is not counted: the candidate stays and is retried (`orphan_stop_cancel_failed`). Any unexpected exception is caught, counted as fail-closed and logged `orphan_stop_sweep_failed` - the safety net must never become the thing that breaks a monitoring cycle.

**Operator view.** `/api/live-trading/metrics` → `protection`: `orphan_stop_sweep_enabled`, `orphan_stop_sweep_runs_total`, `orphan_stop_candidates` (awaiting their next confirmation), `orphan_stops_cancelled_total`, `orphan_sweep_fail_closed_total`. A non-zero `fail_closed_total` is never ignorable. Every cancellation batch also sends the critical alert `Сняты бесхозные стоп-ордера` naming the removed ids, because the positions behind them have no protection any more. All knobs are read on every pass, so a runtime change in `LIVE_TRADING` applies to the next sweep without a restart; `_validate_config()` rejects a non-boolean `orphan_stop_sweep_enabled`, a `orphan_stop_confirmations` below 1 and a negative `orphan_stop_max_cancels` at startup.

**Shutdown (RC2).** `shutdown()` passes `force=True` to `_cancel_pending_stops()`: an entry exists only after `PostStopOrder` returned the new id, so the replacement stop is already at the broker and dropping the superseded one on the way out cannot leave a position unprotected - while keeping it leaves a naked sell stop that no living process owns any more.

**Tests.** `cd backend && python -m pytest -q tests/test_live_executor.py -k "orphan or shutdown_drops"` (confirmations and their reset, interval, disable, every skip reason, empty universe, DB ownership, OCO/amend claims, broker holdings, grace window, stop-book and portfolio outages, rejected cancel, cap / fail-closed, alert emission and throttling, runtime knob re-read, the empty-book cycle, the forced shutdown cancel, defaults / clamping / startup rejection of the knobs) plus `tests/test_live_alerting.py` for the metrics contract.

### 46.14 The canary contour: one lot of SBER behind two operator pauses (Issue #194)

**Why.** The first trade on real money has to be the smallest possible proof of the whole chain instead of a strategy session: ONE ticker, ONE lot, ONE open position, and a human who confirms the only order twice - once before anything reaches the broker, once after the fill is protected. Everything else stays the shipped contour: the same `LIVE_RISK` gates (decision D3 - the canary does not invent its own risk limits), the same #175 broker protection, the #199 orphan sweep, the #178 kill switch and the #193 no-flatten shutdown.

**The policy lives in one place.** `trading_config.CANARY` (`enabled=False`, `ticker='SBER'`, `max_lots=1`, `max_open_positions=1`, `allow_outside_entry_window=False`), the ranges `CANARY_BOUNDS` (`max_lots` and `max_open_positions` inside [1, 100]) and the env map `CANARY_ENV` - four knobs (`CANARY_ENABLED`, `CANARY_TICKER`, `CANARY_MAX_LOTS`, `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW`), because `max_open_positions` is config-only: a canary holding two positions is not a canary. `get_canary_config()` returns an isolated copy and merges defaults → env → a caller dict; a **blank** knob counts as unset and keeps the default, a **malformed** one raises `ValueError` at startup (`_env_strict_bool` for the flag, `normalize_canary_ticker` for the name, `_env_bounded_int` for the cap), so the contour never starts on a guess. `validate_canary_values()` applies the same rules to a caller-supplied dict (tests and drill): `canary={'enabled': True, 'max_lots': 999}` is refused, a partially filled dict is not.

**What the executor does differently** - all of it behind `canary_enabled`, an ordinary run stays byte-identical to #199:

* construction logs `CANARY MODE ON: ticker=... max_lots=... max_open_positions=... confirmations=2` and clamps `max_open_positions = min(config, canary)`, so an env `MAX_OPEN_POSITIONS=5` can never widen a canary and a canary can never widen the ordinary contour;
* `initialize()` narrows the universe through `_apply_canary_universe()`: the canary ticker, and only when `trading_universe` already marks it `live_trading_enabled`. A typo in `CANARY_TICKER` therefore leaves the universe EMPTY (`Canary universe is EMPTY: ticker=... is not live-enabled` at ERROR) and the loop places no orders at all - fail-closed, not "trade the default";
* `process_signal()` repeats that gate right after the kill switch and before the session window, the order book, sizing and any broker call: another name returns reason `canary_universe`, so a direct call (the drill, a manual replay) cannot smuggle a second ticker in;
* the #137 session calendar can be lifted **for a canary only** (PO decision of 2026-10-03: `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true`, shipped `false`). `_entry_window_open()` then answers "open" outside [10:00, 19:00) MSK on weekdays, so a weekend / off-exchange canary can enter at all. It is read in exactly three places - the entry gate of `process_signal()`, the `process_latest_bars()` gate of the main loop, and `wait_for_session_open()`, which returns at once instead of sleeping until Monday. Nothing else is relaxed: the kill switch, the canary universe gate, the risk gate, the stale-book and imbalance filters, sizing, the one-lot cap and both operator pauses all still run. Every bypassed **signal** increments `canary_window_bypass_total` (loop polls do not, so a two-hour run cannot inflate it), the first bypass logs `CANARY: entry window bypassed at <MSK> (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true) - the #137 calendar gate ...`, construction logs `CANARY: entries are allowed OUTSIDE the MOEX entry window ...`, and the `live_start` / `live_entry` alerts gain the line `Вход вне окна сессии: разрешён (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true)`. An ordinary run never reads the knob: `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` without `CANARY_ENABLED=true` changes nothing at all;
* the cap is applied **after** the sizer and after the #176 notional gate and only ever shrinks: `Live canary cap: ticker=SBER size_lots=10 -> 1 reason=canary_cap sizer_reason=risk`. The published sizing reason becomes `canary_cap`, so the run report separates "risk allowed 10 lots, the canary cut it to 1" from "risk allowed 1";
* **pause 1** (`_canary_confirm_entry()`, before `execute_order`): `[CANARY <contour>] Готов к покупке N лот(ов) TICKER по ~X руб., стоп=..., тейк=..., дисбаланс стакана=... Подтвердите (y/n, retry - перечитать стакан)`. `retry` re-reads the order-book aggregate and asks again, at most `CANARY_CONFIRM_MAX_RETRIES=3` times, then the entry is dropped. Anything but an explicit yes (`y` / `yes` / `д` / `да`) is a refusal: `canary_rejections_total` grows, the signal is skipped with reason `canary_not_confirmed`, **zero** mutating broker calls happen, and the critical alert `Canary-вход отклонён оператором` is sent. A `confirm_fn` that raises and EOF (closed stdin, a pipe, `docker compose exec` without `-T`, a detached start) both answer "no" - an unread confirmation is never an approval;
* **pause 2** (`_canary_post_entry_gate()`, after the fill and its protection): `y` keeps monitoring the position; `retry` re-posts ONLY the missing leg through `_canary_rearm_protection()` - an already armed broker stop is never duplicated, because a second active SELL stop would over-sell the position on the next dip and become the next #199 orphan; `n`, or the retry limit, calls `_canary_abort()`.

**An abort is not a flatten.** `_canary_abort()` logs `CANARY ABORT: position_id=... ticker=... lots=... status=... stop_armed=... take_placed=...` at CRITICAL, counts `canary_aborts_total`, sends the critical alert `Canary остановлен оператором — позиция НЕ закрыта` (its last line points at `handover.md §46.11 - ручное закрытие`), sets `shutdown_requested` through `request_shutdown()` and returns `executed=True, reason=canary_aborted` - the order really did happen, the report must not read "opened". It also sets `_canary_abort_no_flatten`, which forces `close_positions_on_shutdown` OFF inside `shutdown()` (`Canary abort: close_positions_on_shutdown forced OFF - the open position keeps its broker protection and waits for the runbook`) even on a deployment that flattens: the money is already in the market with broker protection armed, and liquidating it because a prompt was refused would be the one irreversible action the operator did not ask for. The position is closed by its own stop/take or by runbook C of §46.11.

**Operator view.** `get_metrics()` publishes the identity fields `canary_enabled` / `canary_ticker` / `canary_max_lots` - `None`, never a fake zero identity, when the canary is off - plus the six counters `canary_capped_total`, `canary_rejections_total`, `canary_confirmations_total`, `canary_confirm_retries_total`, `canary_aborts_total`, `canary_window_bypass_total` and the flag `canary_allow_outside_entry_window` (`None`, not `False`, when the canary is off). `GET /api/live-trading/metrics` folds them into their own `canary` section (`enabled`, `ticker`, `max_lots`, `capped_total`, `rejections_total`, `confirmations_total`, `confirm_retries_total`, `aborts_total`, `allow_outside_entry_window`, `window_bypass_total`) instead of hiding them in `risk`: an operator must see "this loop may only ever buy 1 lot of SBER" at a glance. Read the counters with their semantics - `confirmations_total` counts prompts a human **answered** (a refusal is an answer too), `rejections_total` counts refused entries, `capped_total` counts sizes the cap cut - so one refused request for 10 lots moves all three at once. The `live_start` / `live_entry` alerts gain the `_canary_lines()` block (`Canary: включён`, `Canary-тикер`, `Canary-лимит`, `Подтверждения: 2 паузы: перед ордером и после защиты`, `Сайзер`, `Стоп у брокера`, `Тейк у брокера`, plus `Вход вне окна сессии: разрешён (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true)` when the calendar bypass is on) only inside the canary; an ordinary run appends nothing and keeps the #177 body.

**Running a canary.** The confirmations are read from stdin, so the run is a foreground run with the operator at the terminal, inside the entry window (10:00-19:00 MSK). A weekend / off-exchange run needs one extra knob - `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` (PO decision of 2026-10-03); without it every signal is skipped as `outside_entry_window` and the run places nothing:

```bash
# 1. preflight must agree with the contour you intend to trade
docker compose exec -T backend python -m app.analytics.live_executor_preflight
#    on the real contour: PREFLIGHT_EXPECT_CONTOUR=real (see §46.3 step 3)

# 2. foreground start; DURATION_MINUTES is the first argv of `python -m`
docker compose exec -T -e CANARY_ENABLED=true -e CANARY_TICKER=SBER \
  -e CANARY_MAX_LOTS=1 backend python -u -m app.analytics.live_executor 120

# 2b. weekend / off-exchange canary: the same run plus the #137 calendar bypass
docker compose exec -T -e CANARY_ENABLED=true -e CANARY_TICKER=SBER \
  -e CANARY_MAX_LOTS=1 -e CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true \
  backend python -u -m app.analytics.live_executor 120
```

The first log line to verify is `CANARY MODE ON: ticker=SBER max_lots=1 max_open_positions=1 confirmations=2`, then `Canary universe narrowed: ... -> SBER (max_lots=1)`. Without them the process is an ordinary loop and trades the whole universe. A bypassed run additionally logs `CANARY: entries are allowed OUTSIDE the MOEX entry window (CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true) ...` at construction and `CANARY: entry window bypassed at <MSK> ...` at the first signal it lets through - and the weekend run still needs fresh books (`run_data_refresher` / `run_online_data`), because a stale one is rejected as `stale_or_missing_orderbook` exactly as on a weekday.

**Drill (Issue #194).** `194-canary-drill.py` lives with its artifacts in `reports/190-production-trading-infrastructure/194-issue-194-canary-1-lot-sber/` (not part of the image, never in the trading path). Ten phases: `environment` → `connections` → `policy_failfast` → `window_bypass` → `universe` → `cap` → `confirmations` → `abort_without_flatten` → `metrics_alerts` → `full_chain` (opt-in). It is sandbox-only by construction: when `create_execution_client()` resolves to the real contour it stops with `DRILL_BLOCKED` (exit 3) before touching anything. Outside `--full-chain` the four mutating broker methods (`execute_order`, `cancel_order`, `post_stop_order`, `cancel_stop_order`) are shadowed **on the instance** by counting guards verified by marker (never by a call-probe against a real client), the operator answer is hard-wired to `n`, and nothing is written to the database. `--full-chain` additionally requires `--i-understand-sandbox-order`, refuses to start when `live_kill_switch` is ON or any `pending`/`open` row exists, places exactly one `CANARY_MAX_LOTS`-lot order through the shipped `process_signal`, verifies the protection at the broker and never flattens on its own (`--cleanup` flattens a *completed* entry through the shipped shutdown path, never after an abort). Exit codes: `0` `DRILL_OK` / `1` `DRILL_FAIL` / `3` `DRILL_BLOCKED`.

```bash
cd reports/190-production-trading-infrastructure/194-issue-194-canary-1-lot-sber
docker compose cp 194-canary-drill.py backend:/tmp/194-canary-drill.py

# hermetic: fakes only, no DB / API / broker - safe anywhere
docker compose exec -T backend python /tmp/194-canary-drill.py --self-test

# real sandbox drill: live API, live database, read-only broker calls, no order
docker compose exec -T backend sh -c \
  'python /tmp/194-canary-drill.py --json /tmp/drill-194.json > /tmp/drill.txt 2>&1; echo exit=$?'
docker compose cp backend:/tmp/drill.txt      <issue-dir>/drill-sandbox.txt
docker compose cp backend:/tmp/drill-194.json <issue-dir>/drill-sandbox.json
```

Status 2026-10-03 (`--self-test`, `194-self-test.json`): `DRILL_OK`, exit `0`, 9 phases / 238 checks / **0 failed**, `full_chain` skipped by design, all four mutating guards `blocked`, `mutating_attempts` empty, contour `fake`. Position ids are read from the executor's own answer instead of being hardcoded, so the log assertions (`Canary retry: stop re-arm position_id=... -> armed`, `CANARY ABORT: position_id=...`) hold on any database; the refusal case asserts `capped_total=1`, `rejections_total=1` and `confirmations_total=1` together, which is exactly the counter semantics above.

Status 2026-10-03 (sandbox drill inside the rebuilt container, `drill-sandbox.txt` / `drill-sandbox.json`): `DRILL_OK`, exit `0`, 9 phases / **247 checks / 0 failed**, `contour=sandbox`, `full_chain` skipped because it was not requested, all four mutating guards `blocked`, `mutating_attempts` empty, the `live_kill_switch` row `false`, and the deployed universe of 12 tickers narrowed to `SBER`. The `metrics_alerts` phase read the **live** `GET /api/live-trading/metrics` and confirmed the deployed section as `{'enabled': False, 'ticker': None, 'max_lots': None, 'capped_total': 0, 'rejections_total': 0, 'confirmations_total': 0, 'confirm_retries_total': 0, 'aborts_total': 0}` - the shape an ordinary loop publishes, with no fake canary identity.

Status 2026-10-03 (after the calendar-bypass change, in the rebuilt image; `drill-selftest-image-bypass.txt/.json`, `drill-sandbox-image-bypass.txt/.json`): `--self-test` → `DRILL_OK`, exit `0`, **10 phases / 268 checks / 0 failed** - the new `window_bypass` phase contributes 23 of them; the sandbox drill against the live API → `DRILL_OK`, exit `0`, **10 phases / 279 checks / 0 failed**, `contour=sandbox`, all four mutating guards `blocked`, `mutating_attempts` empty, and the deployed `canary` section now reads `{'enabled': False, 'ticker': None, 'max_lots': None, 'capped_total': 0, 'rejections_total': 0, 'confirmations_total': 0, 'confirm_retries_total': 0, 'aborts_total': 0, 'allow_outside_entry_window': False, 'window_bypass_total': 0}`.

**Known limitations.**

- The confirmations are stdin-only: a detached start, `nohup`, `docker compose exec` without `-T` or any pipe answers EOF, and EOF is "no". A canary therefore cannot run as a background service - that is the point, not a bug.
- `CANARY_CONFIRM_MAX_RETRIES=3` is a module constant, not a knob. After three `retry` answers the pause gives up: the first one drops the entry, the second one aborts the stream. An operator who cannot arm the protection in three tries needs runbook C of §46.11, not another prompt.
- The cap is a ceiling in **lots**, not in notional: `MAX_POSITION_SIZE` and the rest of `LIVE_RISK` apply unchanged (decision D3), so a canary on an expensive name is still limited by the ordinary risk gates - and the cap never widens a size the sizer already cut.
- The canary is a mode of the executor process, not persisted state: after a restart the runbook must set `CANARY_ENABLED` again, and an ordinary loop keeps publishing `canary_enabled=false` with `ticker` / `max_lots` = `null`.
- A weekend / off-exchange canary (`CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true`) still needs fresh data and a broker that accepts that session: the bypass removes the **calendar** gate only, so a stale book is still rejected as `stale_or_missing_orderbook` (older than `ORDERBOOK_IMBALANCE.max_age_minutes`) exactly as on a weekday, and thin weekend liquidity means wider slippage on the one lot. Start `run_data_refresher` / `run_online_data` and re-read the preflight (`fresh_orderbooks`, `paper_processes`) before the run.
- `full_chain` is the only code path in the drill that may place an order and it needs two explicit flags plus a clean account; the other nine phases cannot mutate the broker or the database at all.

**Tests.** `cd backend && python -m pytest -q tests/test_live_executor.py -k "canary or confirm or abort or cap"` (shipped defaults and the isolated copy, env overrides plus every fail-fast branch, blank-means-unset, ticker normalisation, caller-dict precedence over env, the 1-lot cap and "only ever shrinks", `min(canary, risk)` open positions, universe narrowing and its fail-closed branch, the direct-call `canary_universe` refusal, both pauses including a raising `confirm_fn` and a closed stdin, the retry limit, the re-arm of the missing leg only, the abort without flatten and its forced-OFF shutdown flag, the counters in the snapshot, the alert lines, and the #137 calendar bypass of the PO decision of 2026-10-03 - default OFF, strict-boolean env, a Saturday entry still capped at one lot and still behind both pauses, the main-loop gate, `wait_for_session_open`, containment of the ordinary contour, `canary_window_bypass_total` and the `Вход вне окна сессии` alert line) plus `tests/test_live_alerting.py` for the `canary_*` snapshot fields and the `canary` section of `GET /api/live-trading/metrics`.
