# The broker layer: sandbox and the real contour (Issue #178, Epic #172 block F)

> **Source:** project-context.md sections 24
> **Last refreshed:** 2026-10-04, task-346

## 24. The broker layer: sandbox and the real contour (Issue #178, Epic #172 block F)

Before #178 execution existed only against the sandbox. The broker layer is now
three modules and one selection point:

```
backend/app/broker/
├── tinkoff_sandbox.py  TinkoffSandboxClient -> client.sandbox.*  (INVEST_GRPC_API_SANDBOX)
├── tinkoff_live.py     TinkoffLiveClient    -> orders / stop_orders / operations / users
└── client_factory.py   create_execution_client() - the only contour selection
```

`LiveExecutor` does not know which client it trades through: the contract is
duck-typed and matches method for method (`execute_order`, `cancel_order`,
`get_orders`, `post_stop_order`, `get_stop_orders`, `cancel_stop_order`,
`get_operations`, `get_positions`, `check_balance`), and the returned structures
are the same dataclasses (`tinkoff_live` re-exports them as
`LiveOrder = SandboxOrder` and so on). The real contour's errors inherit the
sandbox errors (`LiveAPIError(SandboxAPIError)`,
`LiveConfigurationError(SandboxConfigurationError)`), so not one
`except SandboxAPIError` in the executor had to change. The retry policy, the
`Quotation` conversion and the string-to-SDK-enum maps are shared (imported from
the sandbox module - there is no second copy).

### 24.1 Service mapping

| Operation | sandbox | real contour |
|---|---|---|
| Order | `sandbox.post_sandbox_order` | `orders.post_order` (idempotency: `idempotence_id`) |
| Cancel order | `sandbox.cancel_sandbox_order` | `orders.cancel_order` |
| Resting orders | `sandbox.get_sandbox_orders` | `orders.get_orders` |
| Stop order | `sandbox.post_sandbox_stop_order` | `stop_orders.post_stop_order` |
| Stop list | `sandbox.get_sandbox_stop_orders` | `stop_orders.get_stop_orders` (no date filter) |
| Cancel stop | `sandbox.cancel_sandbox_stop_order` | `stop_orders.cancel_stop_order` |
| Operations/fills | `sandbox.get_sandbox_operations` | `operations.get_operations` |
| Portfolio | `sandbox.get_sandbox_portfolio` | `operations.get_portfolio` |
| Cash | `sandbox.get_sandbox_positions` | `operations.get_positions` |
| Accounts | `sandbox.get_sandbox_accounts` | `users.get_accounts` |

Every real service method is keyword-only in `t-tech-investments` 1.51.0 and none
of them accepts a `request=` payload object (only `SandboxService` does); the
idempotency key of `orders.post_order` is `order_id`. Issue #192 found four call
sites violating this: the client authenticated and read the real account, yet every
order and stop-order call died with `TypeError` before the request left the process
- invisible to fakes that accepted any keyword. `backend/requirements.txt` therefore
pins `t-tech-investments==1.51.0` (and `sqlalchemy<2.1`, see §46.7 of the handover),
and `tests/test_tinkoff_live.py` binds every broker call against the installed SDK
signature through `sdk_bound_stub()`.

### 24.2 The contour selection point

The single source of truth is `SANDBOX_TRADING.allow_real_trading`
(`trading_config.py`), `False` in code. The `ALLOW_REAL_TRADING` env override is
resolved in `get_sandbox_trading_config()` through `_env_strict_bool()`: an
ambiguous value raises `ValueError` at startup. The factory
`create_execution_client()` returns `TinkoffLiveClient` (WARNING log) or
`TinkoffSandboxClient` (INFO log); the selected contour is published as
`broker_contour` in the metrics snapshot and in the alert titles. With the gate
open the sandbox client refuses to be constructed, so the two contours cannot be
mixed inside one process. Credentials are separated: `TINVEST_TOKEN`/`TINVEST_ACC`
(market data), `TINVEST_SANDBOX`/`TINVEST_SANDBOX_ACC` (sandbox),
`TINVEST_LIVE_TOKEN`/`TINVEST_LIVE_ACC` (real); cross-fallback is refused in code.
Since Issue #192 one physical token may serve market data and the real contour, but
only through the explicit `ALLOW_LIVE_TOKEN_REUSE=true` opt-in (`False` in code,
strict parsing, WARNING logged); refusing identical values stays the default.

### 24.3 The global kill switch

`trading.app_settings.live_kill_switch` (migration `20260928_001`, runtime seed in
`live_schema.LIVE_SCHEMA_STATEMENTS`, the key is part of
`REQUIRED_APP_SETTINGS_KEYS`). The gate sits in `process_signal` as the **first**
business check - before the session window, the order book, sizing and any broker
call; a rejection is logged with the reason `kill_switch` and counted in
`kill_switch_rejections_total`. Fail-safe (decision D2): a missing row, a `NULL`
or a read error means ON; the in-memory default is `true` too, and `initialize()`
reads the stored value silently before the first cycle. Open positions are
untouched: stops are not cancelled and there is no flatten. Operation is through
`POST /api/live-trading/kill-switch` (upsert plus a read-back confirmation, `503`
when the table is unwritable) or directly by SQL; the state is published as the
`global_kill_switch` section of `GET /api/live-trading/metrics`, and `state`
becomes `kill_switch` when either lever is engaged.

### 24.4 Deploy and migrations

Migrations are an explicit deploy step (decision D4): the one-shot `migrate`
service (`alembic upgrade head`) in `docker-compose.yml`, which `backend` waits
for through `depends_on: {migrate: {condition: service_completed_successfully}}`.
The image carries `alembic.ini` and `alembic/`. Both services share one
environment block (the `x-backend-env` YAML anchor), and
`get_app_database_url()` resolves the password as `POSTGRES_PASSWORD` ->
`PSTGRS_PWD` -> `app`, so the migration DSN and the application DSN are the same.
There is no auto-migration in application code; `.env.example` is now tracked in
git through the `!.env.example` exception (closing D14 of #176).

**Tests:** `test_tinkoff_live.py` (51), `test_live_kill_switch.py` (41),
`test_deploy_migrations.py` (15). Operational details and the go-live runbook:
`handover.md` §46.

### 24.5 Verification tooling (Issue #192)

The broker layer has two read-only diagnostics, kept with the working artifacts in
`reports/190-production-trading-infrastructure/192-g1-production-client-verify/`:

- `192-contract-check.py` - verifies the safety contract without credentials and
  without network calls: the global gate is closed, the factory defaults to the
  sandbox client, a forced real construction fails closed, live/sandbox method
  and keyword parity holds, mutating vs read-only methods are classified
  correctly, live error types inherit the sandbox ones.
- `192-live-smoke.py` - read-only smoke of the real contour with a `--self-test`
  dry run on an in-process fake client. The diagnostic constructor argument
  `allow_real_trading=True` is used instead of flipping `ALLOW_REAL_TRADING`, and
  the mutating methods are shadowed by raising guards before the first API call;
  only `get_accounts` / balance / positions / orders / stop orders / operations
  are read. Tokens and account ids are masked in every artifact.

Run order, masking rules and the current blocked status: `handover.md` §46.10.

### 24.6 Stream shutdown without flatten-all: the three levers (Issue #193)

Epic #190 keeps flatten-all out of the product: stopping the stream never sells
anything. Three independent levers stop it, all shipped earlier (#151, #174,
#178), and they differ in what they touch:

| Lever | Source of truth | New entries | Trailing ratchet | Open positions | Broker stops |
|---|---|---|---|---|---|
| Global kill switch | `trading.app_settings.live_kill_switch`; `POST /api/live-trading/kill-switch` | blocked, skip reason `kill_switch`, counted in `kill_switch_rejections_total` | keeps working | untouched | untouched |
| Trailing kill switch | `trading.app_settings.trailing_kill_switch`; SQL only, no API endpoint | allowed | frozen: no arming, no ratchet, no broker amend | untouched | untouched |
| SIGTERM / SIGINT | `install_signal_handlers()` → `shutdown_requested` → `shutdown()` | no process, no entries | stops with the process | untouched, no flatten (`close_positions_on_shutdown=false`) | stay armed; only pending entry orders are cancelled and marked `cancelled` |

Both switches are re-read every cycle by `_refresh_kill_switch()` (≤
`check_interval_seconds`, 30 s), so neither needs a restart, and every transition
is a one-shot Telegram event carrying its own provenance string. The fail-safe
rules are deliberately different: an unreadable `live_kill_switch` (missing row,
`NULL`, DB error) means ON, while a missing `trailing_kill_switch` row keeps its
historical fail-open `False` and only a DB error turns it ON.

`shutdown()` is the only place that cancels anything, and it cancels pending
entries only: for an `open` row with `close_positions_on_shutdown=false` it logs
`Position <id> left protected with broker_stop_id=...`, writes nothing to
`live_positions`, releases advisory lock `151001` and forces the final metrics
flush (decision D5). A restart restores everything from the database. A position
closed by hand in the broker application is reconciled on the next cycle through
`GetOperations` and `_classify_exit_reason`, and lands as `closed_take` /
`closed_broker` - the frozen #173 status list has no "manual" reason, so
`exit_price_actual` / `lots_executed` are the trustworthy fields.

Verification: `193-shutdown-drill.py` in
`reports/190-production-trading-infrastructure/193-g2-stream-shutdown-no-flatten/`
- sandbox-only by construction, every mutating broker method guarded, the
`live_kill_switch` row restored after the round trip, `DRILL_OK` on 2026-09-30 with
`mutating calls: 0` - plus the #174/#151/#178 regressions. The operator matrix,
runbooks A/B/C and the drill run order: `handover.md` §46.11.


### 24.7 Account-wide orphan stop sweep (Issue #199)

`LiveExecutor` closes the loop the #193 drill opened. `_sweep_orphan_stops()` is the last pass of `_finish_monitor_cycle()` - so it also runs on the empty-book early return, when every stop still sitting at the broker belongs to nobody - and cancels ACTIVE SELL stop orders that protect no live position any more. Eligibility is a single fail-closed chain (`_orphan_stop_skip_reason`): the stop must be ACTIVE, a SELL/STOP_LOSS, inside the configured live universe (ticker / FIGI / instrument_uid), referenced by no `pending`/`open` `live_positions` row, claimed by neither the #175 OCO pass nor either side of a pending amend, not backed by a broker holding of that instrument, and older than `orphan_stop_grace_seconds`. Anything else is kept and logged with its reason.

Acting needs evidence from two directions in time: the same orphan must be reported by `orphan_stop_confirmations` consecutive sweeps (the candidate table is replaced wholesale each pass, so a flapping stop never accumulates confirmations), and a pass that finds more confirmed orphans than `orphan_stop_max_cancels` cancels nothing at all and trips `orphan_sweep_fail_closed_total` plus a throttled critical alert - a stop book full of orphans means the model of the account is wrong, and cancelling is how that mistake becomes real money. Knobs live in `LIVE_TRADING` (`orphan_stop_*`, re-read every pass, validated at startup), counters are published under `protection.orphan_*` of `/api/live-trading/metrics`, and `shutdown()` now passes `force=True` to `_cancel_pending_stops()` so an amend-trailing stop that was superseded but never confirmed is still removed on the way out instead of becoming the next orphan. Operational detail: `handover.md` §46.12.


### 24.8 The canary contour: one ticker, one lot, two operator pauses (Issue #194)

`CANARY` in `trading_config.py` is the whole policy (`enabled=False`, `ticker='SBER'`, `max_lots=1`, `max_open_positions=1`, `allow_outside_entry_window=False`), with `CANARY_BOUNDS` for the two integer ranges and `CANARY_ENV` for the four env knobs (`CANARY_ENABLED`, `CANARY_TICKER`, `CANARY_MAX_LOTS`, `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW`). A project red line forbids a hardcoded ticker anywhere else, so widening the canary is a reviewed config change and widening *past* it needs the CEO go/no-go of the canary report first. `get_canary_config()` merges defaults → env → a caller dict and validates through `normalize_canary_ticker()` / `validate_canary_values()`: a blank knob means unset, a malformed one raises `ValueError` at startup, so the contour never starts on a guess about which single name or which cap it trades.

`LiveExecutor` resolves that policy *before* `_validate_config()` (a canary narrows `max_open_positions` to `min(config, canary)`, never the reverse) and then behaves like the shipped contour with four extra belts. `_apply_canary_universe()` narrows the live universe to the single canary ticker and fails closed to an EMPTY universe when `trading_universe` does not mark it live-enabled; `process_signal()` repeats that gate right after the kill switch and before the session window, the order book, sizing and any broker call (reason `canary_universe`), so a direct call cannot smuggle another name in; the entry size is capped after the sizer and after the #176 notional gate with its own reason code `canary_cap` (the cap only ever shrinks and never touches the `LIVE_RISK` limits - decision D3); and two blocking pauses read an answer through the injectable `confirm_fn` (default `_stdin_confirm`, where EOF is "no") - `_canary_confirm_entry()` before `execute_order` and `_canary_post_entry_gate()` after the fill and its protection, where `retry` re-posts only the missing leg (`_canary_rearm_protection()` never duplicates an armed stop) and a refusal calls `_canary_abort()`. One belt goes the other way and is opt-in (PO decision of 2026-10-03): `CANARY_ALLOW_OUTSIDE_ENTRY_WINDOW=true` lets **this canary only** enter outside the #137 calendar ([10:00, 19:00) MSK on weekdays), which is what a weekend / off-exchange run needs. `_entry_window_open()` is the single reader - the entry gate of `process_signal()`, the `process_latest_bars()` gate of the main loop and `wait_for_session_open()`, which then returns at once instead of sleeping until Monday; the shipped default is `false`, an ordinary run never reads the knob, and every other gate (kill switch, canary universe, risk, book freshness, imbalance, sizing, the one-lot cap, both pauses) still applies.

The abort is the #193 asymmetry in canary form: `request_shutdown()` plus `_canary_abort_no_flatten`, which forces `close_positions_on_shutdown` OFF inside `shutdown()` so the filled position keeps its broker stop/take and waits for the manual runbook instead of being liquidated because a prompt was refused. The counters (`canary_capped_total`, `canary_rejections_total`, `canary_confirmations_total`, `canary_confirm_retries_total`, `canary_aborts_total`, `canary_window_bypass_total`) and the identity fields (`canary_enabled`, `canary_ticker`, `canary_max_lots`, `canary_allow_outside_entry_window` - `None`, not `0`, when the canary is off) go into `get_metrics()`, into the persisted snapshot of §23 and into their own `canary` section of `GET /api/live-trading/metrics`; `_canary_lines()` appends the canary block to the `live_start` / `live_entry` alerts only inside the canary. The sandbox drill (`194-canary-drill.py`, ten phases, mutating broker methods shadowed by counting guards) and the operator runbook live in `handover.md` §46.14.
