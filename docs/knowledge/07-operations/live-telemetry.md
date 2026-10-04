# Live Telegram alerting and executor metrics (Issue #177, Epic #172 task E)

> **Source:** project-context.md sections 23 + handover.md sections 45
> **Last refreshed:** 2026-10-04, task-346

## 23. Live Telegram alerting and executor metrics (Issue #177, Epic #172 task E)

Completed 2026-09-27. The live executor now reports outward: event-driven Telegram alerts,
a periodic heartbeat, and a persisted metrics snapshot served by
`GET /api/live-trading/metrics`. The paper contour (`paper_trader`) and `TelegramNotifier`
itself are unchanged - live uses the notifier as a library. Operational details and commands:
`handover.md` §45.

**Why this shape**: before #177 everything the live contour knew stayed in the container log,
and the #174 heartbeat lived in process memory only, so "the executor crashed" and "the
executor is idle" were indistinguishable. Snapshot persistence (decision D1) went into
`trading.app_settings` as a single JSONB row `live_executor_metrics` - no new table, no
migration: the key (21 characters) fits `key VARCHAR(64)` and `value JSONB NOT NULL` fits as
is; `test_live_schema.py` only checks `REQUIRED_APP_SETTINGS_KEYS`, so a new key does not
break it.

**Configuration** (`trading_config.py`, the `LIVE_ALERTING` section + `get_live_alerting_config()`):
- `heartbeat_interval_seconds`: `3600`, env `LIVE_HEARTBEAT_INTERVAL_SECONDS`, range `(1, 86400]`.
- `heartbeat_stale_seconds`: `300`, env `LIVE_HEARTBEAT_STALE_SECONDS`, range `(1, 86400]`.
- `alert_debounce_seconds`: `300`, env `LIVE_ALERT_DEBOUNCE_SECONDS`, range `(0, 86400]`.
- `slippage_alert_bp`: `50.0`, env `LIVE_SLIPPAGE_ALERT_BP`, range `(0, 10000]`.
- `max_consecutive_errors`: `5`, env `LIVE_MAX_CONSECUTIVE_ERRORS`, range `[1, 100]` - the
  single source of truth replacing the hardcoded `MAX_CONSECUTIVE_ERRORS` constant (decision
  D3; the constant survives as the fallback default, behaviour without config is unchanged).
- `metrics_flush_seconds`: `300`, env `LIVE_METRICS_FLUSH_SECONDS`, range `(1, 86400]`.
- `metrics_key`: `live_executor_metrics` - the row-name contract, not env-overridable.
- `telegram_alerts_enabled`: `true`, env `LIVE_TELEGRAM_ALERTS` - the master switch; `false`
  keeps every event in the log only.

An unparsable or out-of-range env value raises `ValueError` at read time (the same convention
as #176's `LIVE_RISK`), so a typo in `.env` can never silently mute an operator alert. An
in-memory override `LiveExecutor(alerting={...})` passes exactly the same bounds through
`validate_live_alerting_values()`.

**Credentials** (decision D4): no new env variables. The source is
`config_manager.load_settings().telegram`, i.e. `TGM_TOKEN` / `TGM_CHAT_ID` (legacy `TGM_CHAT`)
from the environment or `backend/config/settings.yaml` - the same source `run_paper_trader`
and `/api/notifications/status` use. `build_default_notifier()` returns `None` when the
credentials are missing or unreadable, so the executor still starts and keeps the events in
the log: a monitoring gap must never block the trading contour.

**Delivery** - `LiveExecutor._notify(event, title, lines, critical=False, dedupe=False, icon=None)`:
it never raises into the trading loop, it is a no-op without a notifier or with the master
switch off, and it keeps the `alerts_attempted/sent/failed/suppressed/skipped_total` counters.
Debouncing (decision D2) applies only to repeating criticals (`equity_snapshot_error`,
`protection_failed:{ticker}`, `invariant_violation:{ticker}` - both invariants,
`trailing_amend_failed:{ticker}`, `oco_orphan`, `oco_orphan_unverified`); the key includes the
ticker, so the windows of different instruments are independent. Rare and one-shot events -
start/stop, entry, exit with slippage, stop ratchet, kill switch, `risk_breach` and its
recovery, heartbeat - are always delivered, so "the process is alive" gets through even during
a storm of criticals. `_alert_text()` renders the body and passes every value through
`escape_markdown`: `send_message` always requests `parse_mode=Markdown`, and an underscore in a
ticker or in a broker error string would otherwise turn the message into a 400.

**Heartbeat**: `_maybe_send_heartbeat()` runs once per `run()` cycle and sends the 💓 "the live
contour is alive" message at most every `heartbeat_interval_seconds`; a delivered heartbeat
immediately calls `_flush_metrics(force=True)`, so the API never shows counters older than the
last "process alive". An interval `<= 0` disables the send while keeping the in-memory
`heartbeat_ts` from #174.
**Persistence** (decision D5): `_flush_metrics()` writes the snapshot on a
`metrics_flush_seconds` schedule rather than every cycle, plus forcibly at three points - a
delivered heartbeat, a kill-switch transition and graceful shutdown (after the advisory lock
is released). The reason: with `check_interval_seconds=30`, writing every cycle would mean
~2880 UPDATEs/day into the table that `_refresh_risk_breach_reset` reads and writes every
cycle anyway. The snapshot is `get_metrics()` (#174-#176) plus provenance fields
(`schema_version=1`, `persisted_at`, `strategy`, `tickers`, `notifier_configured`, the
heartbeat windows, the alert and flush counters, `kill_switch_source`). A failing write
increments `metrics_flush_errors_total`, logs a warning and never propagates into the loop.

**The `GET /api/live-trading/metrics` endpoint** (`live_trading_jobs.py`) reads the
`app_settings` row, computes freshness **at read time** (`source.age_seconds` and `flush_stale`
when the age exceeds `2 × metrics_flush_seconds`; `heartbeat.age_seconds` and `stale` from the
window in the snapshot with a fallback to the current config), reads the kill switch from the
live `trading.trailing_kill_switch` row with priority over the snapshot, adds open positions
from `trading.live_positions` (`open_total`, `protected_total`, `unprotected_total`,
`unprotected_tickers`, `trailing_total`, `items[]`, where protection means "a `broker_stop_id`
exists", #175) and publishes `state` as one word in the order `unknown` → `kill_switch` →
`no_heartbeat` → `stale` → `error_threshold` → `risk_breach` → `running`. Next to it sit
`risk.limits` (the current config) and `risk.snapshot_limits` (what the executor actually ran
with): a difference means the process trades on stale limits, and that is visible instead of
hidden.

Since Issue #200 `loop.stopped_reason` says **why** the executor loop ended
(`max_consecutive_errors` / `session_end` / `duration` / `signal` / `exception`, `null` while it
is still running). The field exists because a contour that halted on a streak of errors has no
heartbeat by definition, so the `state` machine can only report `stale` - and "stale" is exactly
what a killed process looks like too. `stopped_reason` is the difference between "the executor
protected the account by stopping itself" and "the executor died".

A missing, corrupted or empty snapshot is **not** an error: `available=false` plus a `reason`
(`no_snapshot` / `malformed_snapshot` / `empty_snapshot`), because a monitoring endpoint that
500s is worse than one that honestly says "I have nothing". `503` remains the only hard
failure - when `trading.app_settings` cannot be read at all.

**Two reading pitfalls** found only against the real database (every fake-backed unit test was
green):
1. `SelectResult.to_dataframe()` normalizes JSONB with `astype(str)`, so the snapshot arrives
   as a Python repr (`"{'schema_version': 1, ...}"` - single quotes, `True`/`None` instead of
   `true`/`null`) and `json.loads` fails → a permanent `malformed_snapshot` in production.
   `_loads_metrics_text()` tries `json.loads`, then `ast.literal_eval` (literals only, executes
   no code); a broken repr still yields `malformed_snapshot`.
2. pandas returns SQL `NULL` as `NaN`/`pd.NA`, and without a guard a missing `broker_stop_id`
   was published as the string `"nan"` while `_metrics_bool(nan)` returned
   `trailing_enabled=true` - an unprotected position looked protected. `_metrics_is_missing()`
   (None/NaN/NA/NaT, no pandas import) is applied in every `_metrics_int/_float/_bool/_text/_datetime`
   coercer and in the `status` filter.

**Anti-drift**: `_METRICS_SNAPSHOT_FIELDS` is the single list of consumed fields; everything
else is published in `extra` through `_json_safe`, so a new executor field is never dropped
silently. `test_the_reader_covers_every_key_the_executor_persists` runs a real
`_flush_metrics(force=True)` and requires `set(written) <= _METRICS_SNAPSHOT_FIELDS` and
`extra == {}`, while `test_the_executor_snapshot_survives_the_dataframe_round_trip` reproduces
the `to_dataframe()` repr path. The web layer deliberately does **not** import
`app.analytics.live_executor`: the shared contract is the row name from
`LIVE_ALERTING.metrics_key`, not the trading-loop code.

**Failure containment**: neither an alert nor a snapshot write can stop trading - both paths are
wrapped in `try/except`, log a warning and increment their own error counter.
`alerts_failed_total` and `metrics_flush_errors_total` are what an operator watches, not the
loop.

**Testing**: `backend/tests/test_live_alerting.py` (167 tests) covers `LIVE_ALERTING` defaults
and env overrides, range validation and `validate_live_alerting_values()`, the `_notify()`
guarantees (a raising notifier, `enabled=False`, the master switch off, per-event-key
debouncing), every event hook, the heartbeat and its throttle, `_flush_metrics()` (throttle,
force points, write failure), the full endpoint response and all of its degradations. The whole
`backend/tests` suite - 765 passed.

**SSL certificates for T-Bank gRPC:** on a Windows host you must explicitly set `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH="$(pwd)/backend/certs/tbank-root.pem"` (set automatically in `start_processes.sh`), otherwise the gRPC connection to `sandbox-invest-public-api.tbank.ru` fails with `CERTIFICATE_VERIFY_FAILED`.

## 45. Operating live Telegram alerting and monitoring (Issue #177)

### Why

Before #177 the live executor only ever spoke to the container log: an un-armed stop, a
broken invariant or a latched drawdown breach went unnoticed, and "the process died"
looked exactly like "the process is idle" - the #174 heartbeat lived in memory and died
with the process. #177 adds three layers: event-driven Telegram alerts, a periodic
heartbeat, and a persisted metrics snapshot with an HTTP reader,
`GET /api/live-trading/metrics`. Architecture details: `project-context.md` §23.

### What changed

- `backend/app/analytics/trading_config.py` - the `LIVE_ALERTING` section (8 keys),
  `LIVE_ALERTING_BOUNDS`, `LIVE_ALERTING_ENV`, `get_live_alerting_config()`,
  `get_live_alerting_bounds()`, `validate_live_alerting_values()`.
- `backend/app/analytics/live_executor.py` - `_alert_text()`, `_notify()`,
  `_maybe_send_heartbeat()`, `_metrics_payload()`, `_flush_metrics()`,
  `build_default_notifier()`, `run_live_executor()`; the `notifier=` and `alerting=`
  constructor arguments; 21 `_notify()` call sites (20 event keys);
  `_max_consecutive_errors` from config instead of the hardcoded constant (decision D3);
  `_kill_switch_source`; the `LIVE_METRICS_KEY = "live_executor_metrics"` and
  `LIVE_METRICS_SCHEMA_VERSION = 1` constants.
- `backend/app/api/live_trading_jobs.py` - `GET /api/live-trading/metrics`
  (`_metrics_endpoint_payload()` plus the `_metrics_*` helpers). The module deliberately
  does **not** import `app.analytics.live_executor`: the web layer must not depend on the
  trading loop, the shared contract is the `app_settings` row key.
- `backend/tests/test_live_alerting.py` - 167 tests: config and validation, `_notify` and
  debouncing, every event hook, the heartbeat, the snapshot flush, the whole endpoint
  response and its degradations.
- `backend/app/notifications/telegram_notifier.py` - `_escape_markdown` became the public
  `escape_markdown` (the private alias is kept for existing callers): the live contour builds
  its own alert body and must escape tickers, reasons and broker error strings exactly like
  the paper helpers do.
- `start_processes.sh` - the executor launch moved from `LiveExecutor().run(...)` to
  `run_live_executor(...)`, so the notifier is wired in the normal launch path too (including
  `SESSION_AWARE=1`) and alerts actually go out.
- `.env.example` - a block of 7 `LIVE_*` variables.

### Configuration (`LIVE_ALERTING`, every key optional)

| Key | Env | Default | Range | Purpose |
|---|---|---|---|---|
| `heartbeat_interval_seconds` | `LIVE_HEARTBEAT_INTERVAL_SECONDS` | 3600 | (1, 86400] | Telegram heartbeat period; `<= 0` disables the send |
| `heartbeat_stale_seconds` | `LIVE_HEARTBEAT_STALE_SECONDS` | 300 | (1, 86400] | Window after which the heartbeat counts as stale |
| `alert_debounce_seconds` | `LIVE_ALERT_DEBOUNCE_SECONDS` | 300 | (0, 86400] | Minimum gap between two alerts sharing a key (`dedupe=True` only) |
| `slippage_alert_bp` | `LIVE_SLIPPAGE_ALERT_BP` | 50.0 | (0, 10000] | Exit slippage worth waking the operator for, bp |
| `max_consecutive_errors` | `LIVE_MAX_CONSECUTIVE_ERRORS` | 5 | [1, 100] | Consecutive-error threshold: stop the loop + critical alert (decision D3) |
| `metrics_flush_seconds` | `LIVE_METRICS_FLUSH_SECONDS` | 300 | (1, 86400] | Period of the scheduled metrics write (decision D5) |
| `metrics_key` | - | `live_executor_metrics` | 1..64 chars | `trading.app_settings` row key; a contract name, not a tunable |
| `telegram_alerts_enabled` | `LIVE_TELEGRAM_ALERTS` | `true` | bool | Master switch: `false` keeps every event in the log only |

An unparsable or out-of-range env value raises `ValueError` at read time, so a typo in
`.env` fails fast instead of silently muting an operator alert. `LiveExecutor` accepts an
in-memory `alerting=` override that goes through exactly the same bounds via
`validate_live_alerting_values()`.

Telegram credentials are **unchanged** (decision D4 - no new env variables):
`config_manager.load_settings().telegram`, i.e. `TGM_TOKEN` and `TGM_CHAT_ID` (legacy
`TGM_CHAT`) from the environment or `backend/config/settings.yaml`.
`build_default_notifier()` returns `None` when the credentials are missing or unreadable,
so the executor still starts and keeps every event in the log.

### Events

Always delivered (`dedupe=False`, rare or one-shot): `live_start`, `live_stop`,
`heartbeat`, `live_entry`, `live_exit`, `slippage_high:{ticker}`, `trailing_step`,
`protection_pending:{ticker}`, `kill_switch_on` / `kill_switch_off`, `risk_breach`,
`risk_breach_cleared`, `risk_breach_restored`, `consecutive_errors`. `risk_breach` is
deliberately not debounced: the latch fires once per session, and "the process is alive"
must get through even during a storm of criticals.

Throttled by `alert_debounce_seconds` (`dedupe=True`, repeats every cycle while the fault
lasts): `equity_snapshot_error`, `protection_failed:{ticker}`,
`invariant_violation:{ticker}` (both invariants), `trailing_amend_failed:{ticker}`,
`oco_orphan`, `oco_orphan_unverified`. The debounce key of a per-ticker event includes the
ticker, so the windows for SBER and PLZL are independent.

`_alert_text()` renders every message: each dynamic value goes through `escape_markdown`
because `TelegramNotifier.send_message` always requests `parse_mode=Markdown`. Without it
an underscore in a ticker or in a broker error string would turn the message into a 400
and the operator would see nothing at all. `None` fields are dropped so callers can pass
optional fields unconditionally.
### Snapshot persistence (decisions D1 + D5)

`_flush_metrics()` upserts one JSONB row of `trading.app_settings` under `metrics_key` -
no new table, no migration. Out-of-schedule writes (`force=True`) happen at the three
points an operator must not lose: a delivered heartbeat, a kill-switch transition, and
graceful shutdown (after the advisory lock is released - the last chance to leave
truthful counters behind). The scheduled write runs once per `run()` cycle and at most
every `metrics_flush_seconds`: with `check_interval_seconds=30`, writing every cycle would
mean ~2880 UPDATEs/day into the table that `_refresh_risk_breach_reset` (#176) reads and
writes every cycle anyway.

The snapshot is `get_metrics()` (#174-#176) plus provenance fields: `schema_version`,
`persisted_at`, `strategy`, `tickers`, `ticker_count`, `notifier_configured`,
`telegram_alerts_enabled`, `max_consecutive_errors`, the heartbeat windows, the
`alerts_attempted/sent/failed/suppressed/skipped_total` counters, `heartbeats_sent_total`,
`metrics_flushes_total`, `metrics_flush_errors_total`, `kill_switch` and
`kill_switch_source`.

A failing write increments `metrics_flush_errors_total`, logs a warning and **never**
propagates into the loop or touches `_consecutive_errors`: monitoring has no right to
stop trading.

### The `GET /api/live-trading/metrics` endpoint

Response: `available`, `reason`, `error`, `state`, `generated_at`, `source`, `loop`,
`heartbeat`, `kill_switch`, `protection`, `risk`, `alerting`, `positions`, `extra`.

- `state` is one word in a deliberate order: `unknown` (no readable snapshot) →
  `kill_switch` → `no_heartbeat` → `stale` → `error_threshold` → `risk_breach` →
  `running`. First "is the process alive at all", then the trading states.
- A missing or corrupted snapshot is **not** an error: `available=false` plus a `reason`
  (`no_snapshot` / `malformed_snapshot` / `empty_snapshot`). `503` is returned only when
  `trading.app_settings` cannot be read at all - there is nothing left to serve then.
- Freshness is computed at read time, never taken from the snapshot: `source.age_seconds`
  and `source.flush_stale` (older than `2 × metrics_flush_seconds`), `heartbeat.age_seconds`
  and `heartbeat.stale` (the window from the snapshot, falling back to the current
  config). Aware stamps collapse to naive MSK with the same `now_msk_naive()` the executor
  uses, so an age can never be negative.
- `kill_switch` is read from the live `trading.trailing_kill_switch` row and takes priority
  over the snapshot; published as `active`, `snapshot_active`, `from_live_row` and `source`
  (`startup` / `app_settings` / `app_settings:missing_key` / `db_error:<Type>`).
- `positions` is a separate query against `trading.live_positions`: `open_total`,
  `protected_total`, `unprotected_total`, `unprotected_tickers`, `trailing_total`,
  `items[]`. Protection means "a `broker_stop_id` exists" (#175); trailing is counted
  separately. A failure of that query degrades this block only, not the whole response.
- `risk.limits` is the current config, `risk.snapshot_limits` is what the executor actually
  ran with; when they differ the process is trading on stale limits and the panel must
  show that instead of hiding it.
- `extra` is the anti-drift guard: everything not listed in `_METRICS_SNAPSHOT_FIELDS` is
  published. A new executor field shows up immediately instead of being dropped silently;
  `test_the_reader_covers_every_key_the_executor_persists` runs a real
  `_flush_metrics(force=True)` and requires `set(written) <= _METRICS_SNAPSHOT_FIELDS` and
  `extra == {}`.
### Behaviour an operator must know

- **`to_dataframe()` normalizes JSONB into a Python repr.** `DBManager.select()` returns
  the dict as-is, but `.to_dataframe()` applies `astype(str)`, so the snapshot arrives as a
  string like `"{'schema_version': 1, ...}"` - single quotes, `True`/`None` instead of
  `true`/`null`. `json.loads` cannot parse that, so the reader uses `_loads_metrics_text()`:
  `json.loads` first, then `ast.literal_eval` (literals only, executes no code); a broken
  repr still yields `malformed_snapshot`. **Any new JSONB reader must do the same**,
  otherwise production shows a permanent `malformed_snapshot` while every unit test on
  fakes stays green - exactly how this bug looked before the container smoke.
- **pandas returns SQL `NULL` as `NaN` / `pd.NA`.** Without a guard a missing
  `broker_stop_id` was published as the string `"nan"` and `_metrics_bool(nan)` returned
  `trailing_enabled=true`, so an unprotected position looked protected.
  `_metrics_is_missing()` (None / NaN / NA / NaT, no pandas import) is applied in every
  `_metrics_int` / `_float` / `_bool` / `_text` / `_datetime` and in the `status` filter of
  the positions block.
- Alerts **never** break the loop: `_notify()` does not raise, a failed delivery increments
  `alerts_failed_total` and stays in the log, there are no retries. Without a notifier or
  with `LIVE_TELEGRAM_ALERTS=false` it is a no-op and `alerts_skipped_total` grows.
- The alert and heartbeat counters live in process memory and reset on restart. The source
  of truth for "how long ago" is `source.age_seconds` and `heartbeat.age_seconds`, not the
  counters.
- `heartbeat_interval_seconds=3600` with `heartbeat_stale_seconds=300` means the heartbeat
  goes stale between sends in normal operation: `heartbeat.stale=true` on its own is not an
  incident. An incident is `state=stale` / `no_heartbeat` together with a growing
  `source.age_seconds` and `flush_stale=true`. For a "green" monitor set
  `LIVE_HEARTBEAT_STALE_SECONDS` above the send interval.
- The endpoint cannot tell "the executor was stopped on purpose" from "the executor
  crashed": `available=true` survives from the last flush while `state` becomes `stale`.
  An external watcher must key on `state` + age, not on `available`.
- `LIVE_TELEGRAM_ALERTS=false` mutes the events but the metrics snapshot keeps being
  written: `metrics_key` and the flush do not depend on the master switch.
- **The entry point matters.** `run_live_executor()` wires the notifier through
  `build_default_notifier()`; a direct `LiveExecutor().run(...)` does not, so such a process
  trades and writes the metrics snapshot but keeps every event in the log only. A canary that
  must actually deliver to the chat has to use `run_live_executor(...)` or pass
  `notifier=build_default_notifier()` explicitly. `start_processes.sh` now uses
  `run_live_executor`.

### Commands

```bash
# The main monitoring request
curl -s http://localhost:8000/api/live-trading/metrics | python -m json.tool

# State and age on one line
curl -s http://localhost:8000/api/live-trading/metrics | python -c "import sys,json; d=json.load(sys.stdin); print(d['available'], d['state'], d['source']['age_seconds'], d['heartbeat']['age_seconds'], d['positions']['unprotected_total'])"

# The raw snapshot row
psql -c "SELECT key, updated_at, jsonb_pretty(value) FROM trading.app_settings WHERE key='live_executor_metrics';"

# Read-only Telegram connectivity probe (getMe, 30s cache)
curl -s http://localhost:8000/api/notifications/status | python -m json.tool

# Run without Telegram: every event stays in the log
LIVE_TELEGRAM_ALERTS=false python -m app.analytics.live_executor 1

# Canary: short run with a frequent heartbeat and a frequent snapshot write
LIVE_HEARTBEAT_INTERVAL_SECONDS=30 LIVE_METRICS_FLUSH_SECONDS=10 \
  python -m app.analytics.live_executor 1

# Tests (160)
cd backend && python -m pytest tests/test_live_alerting.py -q
```

### Known limitations

- Only a live process writes the snapshot: with the executor down `available=true` remains
  from the last flush while the data goes stale. There is no metrics history table
  (decision D1 - one row per process).
- The Telegram messages themselves are not stored anywhere - only the delivery counters.
- There is no delivery retry: an undelivered critical stays in the container log.
- The debounce window lives in memory (`_alert_last_sent`), so a restart clears it and can
  let a duplicate of the same critical through right after the start.
- The heartbeat is sent from the `run()` loop only; a separate watchdog process (an
  external "the snapshot has not been updated for N minutes" alert) is not part of #177 -
  the deploy step and healthcheck belong to task F (#178).
- The snapshot field names are a contract, not a derivative of the code: the reader does not
  import `live_executor`, so a new field must also be added to `_METRICS_SNAPSHOT_FIELDS`,
  otherwise it lands in `extra` (safe, but untyped).
