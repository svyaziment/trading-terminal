# Agent Handover Guide: Trading Terminal

Last refreshed: 2026-10-03 (task-194 - the canary contour: the `CANARY_*` runbook knobs, the two blocking stdin confirmations, the abort that stops the stream without flatten, the `canary` section of `/api/live-trading/metrics` and the `194-canary-drill.py` sandbox drill; new §46.14 - the number §46.13 is taken by Issue #200 on its own branch); previously 2026-09-30 (task-199 - account-wide orphan stop sweep: the fail-closed eligibility chain of `_sweep_orphan_stops()`, the confirmation counter, the per-pass cancel cap with its fail-closed branch, the `orphan_stop_*` knobs, the `protection.orphan_*` metrics and the forced shutdown cancel of amend-superseded stops; new §46.12); previously 2026-09-30 (task-193 - stopping the stream without flatten-all: the matrix of the three levers, operator runbooks A/B/C, the `193-shutdown-drill.py` sandbox drill and its orphan-stop finding; new §46.11); previously 2026-09-30 (task-192 - real-contour verification: four SDK 1.51.0 call-shape fixes in `TinkoffLiveClient`, pinned `t-tech-investments` / `sqlalchemy`, the `ALLOW_LIVE_TOKEN_REUSE` opt-in, new §46.7 rows); previously 2026-09-29 (task-191 - phantom drawdown triage and the new equity measurement fields in §44); previously 2026-09-28 (task-178 - the real T-Bank contour `TinkoffLiveClient`, the contour factory driven by `ALLOW_REAL_TRADING`, the global kill switch `live_kill_switch` + `POST /api/live-trading/kill-switch`, deploy migrations through the one-shot `migrate` service; new §46); previously 2026-09-27 (task-177-live-start-fix); previously 2026-09-27 (task-177); previously 2026-09-27 (task-176); previously 2026-09-19 (task-174); previously 2026-09-16 (task-151); previously 2026-09-16 (task-150); 2026-09-15 (task-149); 2026-09-15 (task-148); 2026-09-14 (task-147)
This file is the operational guide for agents. Read project-context.md first for architecture.

## 1. Purpose

Operational knowledge to work on this project safely: structure, DB schema, pipeline, API, known issues, roadmap, operational gotchas, and the collaboration protocol (context collection before multi-element tasks).

## 2. Project Structure

See project-context.md section 2 for the full tree. Key operational entry points:
- `backend/app/main.py` - FastAPI app + route registration.
- `backend/app/analytics/trading_config.py` - trading universe, LIVE_UNIVERSE (12-name PO list), and strategy registry (single source of truth).
- `start_processes.sh` / `stop_processes.sh` - paper trading and opt-in sandbox execution processes.
- `docs/refresh/context_collector.py` - context collector for agent tasks.

## 3. Database Schema

See project-context.md section 3. New tables include `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `live_positions`, `trading_universe`, and `alerts`. All are in schema `trading`.

## 4. Data Pipeline

See project-context.md section 4. Four paper processes are started by default:
1. `data_refresher` - MOEX 1min + aggregation + indicators + signals (every 15 min, top-15 ∪ LIVE_UNIVERSE).
2. `online_data` - streaming 1min candles + order book.
3. `live_engine` - reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
4. `paper_trader` - reads strategy config from DB (RR from `config.risk_reward`, trailing from `config.trailing_stop`), alerts -> market positions -> monitor stop/take/trailing (dynamically updates `stop_price` via in-memory `TrailingState`, emits `trailing` exit reason) -> write equity and best-effort Telegram notifications. Records `strategy_name` in `paper_positions`.
- Optional: `LiveExecutor` provides sandbox broker execution and starts only with `START_LIVE_EXECUTOR=1`.
On startup: `position_catchup` resolves pending/open positions against historical candles.

## 5. API Endpoints

See project-context.md section 5. Strategy Lab: `/api/strategies/*`. Paper trading: `/api/paper-trading/*`.

## 6. Patterns

See project-context.md section 6.

## 7. Known Issues & Status

See project-context.md section 7.

## 8. Roadmap Status

See project-context.md section 8.

## 9. Important Notes

See project-context.md section 9.

## 10. Operational Gotchas

- **MSYS path conversion**: use `MSYS_NO_PATHCONV=1` for docker commands with absolute paths in Git Bash. Without it `/app/...` becomes `C:/Program Files/Git/app/...`.
- **Windows Python vs MSYS paths**: `python`/`python3` on the host cannot open MSYS-style absolute paths (`/f/GIT/...`). Pass RELATIVE paths to Python scripts/patches (relative to repo root), or run Python inside the container.
- **stdout pollution**: DBManager logs to stdout. In scripts that parse JSON from stdout, reroute logging to stderr BEFORE importing app. Otherwise "Extra data: line 1 column 5 (char 4)".
- **%% escaping in SQL**: psycopg2 interprets `%` as placeholder start. Escape modulo as `%%`.
- **close_pool()**: never call `db.close_pool()` in FastAPI handlers or long-lived background loops (process-wide pool). Only in standalone scripts that exit. In data_refresher the pool is kept alive across cycles.
- **Heredoc loss**: large bash heredocs can lose blocks when copied in Git Bash. Always verify file size after creation (`wc -c`). If bytes < expected, re-copy.
- **Docker rebuild**: after backend code changes, MUST rebuild (`docker compose up -d --build backend`).
- **Unbuffered logging**: Background processes (start_processes.sh) use `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` for immediate log writing to files. Without this, logs are block-buffered and appear empty until the buffer fills.
- **Overnight LiveExecutor duration (Issue #137)**: without `DURATION_MINUTES`, `start_processes.sh` sizes paper until the **next session open after 19:00** so leftover stop/take still have a live book. LiveExecutor waits for 10:00 and **enters only [10:00, 19:00)**; exits stay price-based after 19:00. A leftover `DURATION_MINUTES=540` from a 23:00 launch still dies at 08:00. Canary still needs an explicit `DURATION_MINUTES=N`. Units: `cd backend && python -m pytest -q tests/test_moex_session.py tests/test_live_executor.py`.
- **JSON NaN / Infinity**: pandas produces NaN/NaT, and a single winning trade yields `pf: Infinity`. Python `json.dumps` writes non-strict JSON that PostgreSQL JSONB rejects (`invalid input syntax for type json`). Sanitize API responses **and** `backtest_results` INSERTs via `_json_dumps` → `_json_safe` in `strategy_jobs.py` (`inf`/`nan` → `null`). Also used for API payloads in `paper_trading_jobs.py`. Cast timestamps to text in SQL (`created_at::text`).
- **JSONB as string**: DBManager returns JSONB columns as Python-repr strings, not dicts. Normalize with `_to_dict` (json.loads, then ast.literal_eval fallback).
- **Backtest matrix runtime**: full matrix takes ~10-15 min. Use quick=true for liveness.
- **Reports mount**: backend mounts `./reports` (docker-compose). Strategy runs write `reports/strategy-lab/last_run.json` - send it on any Strategy Lab error.
- **Resistance-zone veto (Issue #97)**: `levels_reversal` must not enter when the 1min close sits in an active resistance zone, even if `nearest_level_at(..., 'support')` returns a valid support and the 0.5×ATR extension covers the fill. That is a structural defect, not role-reversal (ALRS paper #711: fill 19.80 inside impulse resistance 19.67). Guard: `overlapping_resistance_zone_at` in `StrategyEvaluator.check_entry`. Issue #106: the same function skips non-`active` zones when a `state` column is present. Issue #107: `StrategyEvaluator` passes `LevelsTracker` (and `is_broken`) into the veto only when `level_breakout_retest` is enabled; locked `test_20260731` does not, so paper/live veto is unchanged. Do not rewrite locked `test_20260731`. Units: `cd backend && python -m pytest -q tests/test_resistance_zone_veto.py tests/test_levels_state_machine.py tests/test_level_breakout_retest.py`.
- **Lab plugin HTF (Issue #116)**: Strategy Lab always saves `config.strategy_name = "levels_reversal"`, so `_run_job` calls `run_portfolio_backtest`, not `run_strategy_backtest`. `MarketContext.htf_bars` must carry `build_strategy_context()['htf_bars']` (same TF as the levels). `candles_4h` is usually unset on this path. Without HTF, `LevelsTracker._sync_tracker` exits immediately, every level stays `active`, and any breakout pattern (`level_breakout_retest`, `levels_sr_breakout`) yields **zero trades**. Locked `test_20260731` does not enable breakout, so wiring HTF is a no-op for paper. Units: `cd backend && python -m pytest -q tests/test_strategy_plugin.py tests/test_level_breakout_retest.py`.
- **Composite S/R (Issue #117)**: `levels_sr_breakout` is an **entry engine** (OR of path A support and path B resistance retest), not an AND-filter. Isolated Lab run: `config.patterns` has `levels_sr_breakout` (and optionally `signal_4h_buy`) **without** `levels_reversal`. If both chips are on, the composite wins (one support path). Do not AND with `level_breakout_retest` as a replacement. Trades carry `source` (`levels_sr_breakout_support` / `levels_sr_breakout_resistance`). Locked `test_20260731` must stay off this id. Units: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.
- **AFKS smoke (Issue #119)**: isolated ticker backtest, not a 50k portfolio. Package `analytics/issue-119-afks-sr-breakout-smoke/`. A vs B on AFKS `2024-08-01`…`< 2026-08-21`. B-support n can exceed A because the composite passes `LevelsTracker` into the veto. `run_strategy_backtest` is the source of `source`; `run_portfolio_backtest` currently drops it. Do not lock/overwrite `test_20260731` / `test_20260820` / `test_20260821`. Replay: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py` (needs `results.json`). Units: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.
- **Lab-universe A/B (Issue #124)**: isolated 28-ticker `get_big_tickers` run, same SHA as #119. Package `analytics/issue-124-sr-breakout-universe/`. A n=2559 PF 1.46; B n=4799 PF 1.39 (support 3811 / resistance 988). AFKS matched #119; ALRS 19.80 bar absent. Optional 50k slot replay of B is a separate block, not isolated PF. Do not lock/overwrite the three reference strategies. Replay: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Units: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.
- **Support with tracker (Issue #127 / Epic #126)**: `levels_sr_support` is the **B-support-only** entry engine from #124. Same support geometry as `levels_reversal` plus the #97 veto **with** `LevelsTracker`. No `check_breakout_retest`. Isolated run: `config.patterns` has `levels_sr_support` (optionally `signal_4h_buy`) **without** `levels_reversal` / `levels_sr_breakout` / `level_breakout_retest`. Composite still wins if both engines are on. Do not silently turn the tracker on for locked `test_20260731`. Isolated Lab universe: handover §31 (C n=4380 PF 1.45; exclusive 3811/1.51 is not bit-for-bit). Units: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_strategy_plugin.py`.
- **Isolated support universe (Issue #129)**: package `analytics/issue-129-sr-support-universe/`. Isolated C (`levels_sr_support` + `signal_4h_buy`) on the same 28-ticker Lab universe as #124. Exclusive B-support 3811 / 1.51 is a composite label (path B occupies the slot). Runnable C is 4380 / 1.45. Extra 611: occupancy 610 + leftover 1; missing 42 cascade. AFKS 89 / 1.49 (exclusive 78 ⊆ C). Resistance n=0. ALRS 19.80 blocked. #130 must use C, not exclusive. Do not lock/overwrite the three reference strategies. Replay: `python analytics/issue-129-sr-support-universe/analysis.py`. Units: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.
- **Support portfolio 50k (Issue #130)**: package `analytics/issue-130-sr-support-portfolio/`. Slot replay of published C (4380 candidates, SHA `3b7864c4…aedb1b`), not exclusive 3811/1.51 and not a `source=` filter of #124 B-mix. n=3237 PF 1.33 equity 96,204.63 daily Max DD 6.08% no GAME OVER. ALRS 19.80 absent. Verdict: not paper. Replay: `python analytics/issue-130-sr-support-portfolio/analysis.py`. Units: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.
- **Stepped trailing-stop A/B (Issue #139)**: package `analytics/issue-139-trailing-stop-new-level/`. Analytics-only exit mode in `trailing.py` (configurable steps in R, default +2R→+1.5R, +2.5R→+2R; never touches the production exit path). A/B on locked `test_20260830_new_level` (id=126, RR 1:3), 28 names, full period. `extract_inputs.py` evaluates both exits on the same brain path (DB read-only, resumable, `baseline_replay_mismatches` must be 0); `analysis.py` runs the slot replay + comparison without a DB. Exact figures/verdict: `summary.json` / `report.md`. Units: `cd backend && python -m pytest -q tests/test_issue139_analysis.py`. See handover §34.


- Windows spawn vs shard processes (Issue #147): under a detached (nohup) parent, `ProcessPoolExecutor` workers may fail to start on Windows (`multiprocessing/spawn.py → reduction.duplicate → _winapi.DuplicateHandle → PermissionError [WinError 5]`), and the parent sees BrokenProcessPool on every future; the trigger is unstable (the same nohup+spawn worked hours earlier). For long analytics runs do not rely on multiprocessing: shard tickers into plain OS processes launched from bash (`--stage shard --book X --shard-index i --shard-count N`) and assemble the book from the versioned cache `cache/<book>-<sha8 of config>/` (`--stage assemble`). Liveness monitor: log tail, per-book cache counters, `kill -0 $(cat run.pid)`.

## 11. Collaboration Protocol (agents)

- **Collect context before multi-element tasks.** When a task touches several modules/classes/scripts or their interplay, FIRST collect up-to-date context from the primary sources instead of guessing the implementation:
python docs/refresh/context_collector.py
--task-id task-NNN
--files backend/app/analytics/levels_backtest.py,backend/app/db/db_manager.py
--tables backtest_runs,backtest_trades
--output reports/task-NNN/context.json
  `--files` collects file contents; `--tables` collects schema + row count + sample + date range. Load the resulting `context.json` before implementing.
- **Task scripts live in `scripts/`** (gitignored). Each task writes reports to `reports/<AGENT_NAME>/<ISSUE_NUMBER>_<ISSUE_NAME>/` (see developer-sop.md for naming conventions).
- **Verify after write**: always check file sizes (`wc -c`) and run a build/health check after changes.
- **Docs are bilingual**: keep `*.md` and `*.ru.md` in sync (project-context, handover, strategy docs).

## 12. Operating the Order-book Imbalance Filter

- Entry points: `online_data.save_orderbook_aggregate` calculates and stores each stream update; `orderbook_imbalance.get_recent_imbalance` reads a fresh aggregate; `passes_imbalance_filter` is the mandatory signal gate.
- Infrastructure policy is `ORDERBOOK_IMBALANCE` in `trading_config.py`: depth 10, maximum age 5 minutes, default threshold 1.0. Strategy override: top-level `config.imbalance_threshold`.
- Passing condition is strict: `volume_imbalance > imbalance_threshold`. Missing, stale, null, NaN/infinite data, or zero ask depth always rejects the signal.
- Quick DB diagnostic:
  `SELECT ticker, timestamp, bid_depth, ask_depth, volume_imbalance FROM trading.online_orderbook_aggregates ORDER BY timestamp DESC LIMIT 20;`
- If all live signals are skipped, first confirm that `online_data` is running and the latest row is less than 5 minutes old. Do not weaken the missing-data guard.
- Unit test: `cd backend && python -m pytest -q tests/test_orderbook_imbalance.py`.

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

## 14. Operating Position Sizing

- Entry point: `app.analytics.position_sizer.calculate_position_size`. Pass free capital, stop distance as a percent of entry, entry price, and the instrument's `lot_size`.
- Live defaults come only from `POSITION_SIZING` in `trading_config.py`: 1% risk per trade and 20% maximum concentration. Optional function overrides are intended for tests and simulations.
- Use `size_lots` as the broker order quantity. `size_rub` is the pre-rounding budget, not a fractional-lot instruction.
- `invalid_stop` and `insufficient_capital` are rejection results (`size_lots == 0`) and must not reach the broker. `min_lot` is executable because the calculator has already confirmed that free capital covers one full lot.
- Unit test: `cd backend && python -m pytest -q tests/test_position_sizer.py`.

## 15. Operating the Sandbox Live Executor

- Prerequisites: backend rebuilt, streaming online data running, one active locked strategy, a funded sandbox account, and `LIVE_TRADING.enabled=true`.
- Apply the migration explicitly when provisioning a database: `psql ... -f backend/migrations/20260817_01_live_positions.sql`. `LiveExecutor.initialize()` also applies the same idempotent schema automatically.
- Safe overnight start (Issue #137): rebuild backend, then `START_LIVE_EXECUTOR=1 ./start_processes.sh` with **no** `DURATION_MINUTES`. Paper processes run until the next weekday **10:00** after this session's 19:00 (so leftover protection still has streaming). LiveExecutor sleeps until 10:00 MSK, **enters only 10:00–19:00**, then keeps stop/take until the position closes by price. Clock is the computer clock converted to MSK (UTC+3). `START_LIVE_EXECUTOR=1` remains opt-in so a normal paper launch does not place sandbox orders. Logs: `reports/live-executor/executor.log`. Live-start alert timing (2026-09-27 follow-up to #177): the `live_start` Telegram alert is sent immediately when the process starts - before the overnight wait for the 10:00 MSK session - so a Sunday-evening launch is visible in the chat at once. Because the alert precedes `initialize()`, its ticker count reads 0 and the strategy name is empty until the session opens; treat those two fields as "not yet initialized" in pre-session alerts. `check_interval` is resolved before the alert, so the payload cannot raise.
- Documentation and published artifacts drift apart: when `report.md` and an agent doc disagree about the grid count, the lattice schema or a headline number, trust the artifacts and re-render — `python analytics/issue-143-trailing-robustness/run.py --stage report`, then `cd backend && python -m pytest -q tests/test_issue155_analysis.py`. Never carry lattice numbers out of a PR or commit message.
- Canary / fixed window: `DURATION_MINUTES=N` still starts immediately and stops N minutes after launch. Do not use that for an overnight Sunday→Monday session.
- LiveExecutor entries are gated to [10:00, 19:00) MSK (`reason=outside_entry_window`) even if `StrategyEvaluator.entry_window` is 7–19. Stop/take fire when price hits, after 19:00 as well; the process stops only when the book is flat (or SIGTERM). Shutdown policy is unchanged (`close_positions_on_shutdown=false`).
- Processing order is fixed: `StrategyEvaluator` BUY -> session window -> fresh imbalance -> free RUB -> position sizing -> market BUY -> broker `STOP_LOSS` -> take sell-limit -> DB record/reconciliation.
- Stop protection is a real broker stop order (Issue #175). As soon as the entry fills, `post_stop_order()` submits a `STOP_LOSS` — trigger = model stop aligned down to `min_price_increment`, limit = trigger minus `trailing_protective_ticks` ticks — and stores the id in `broker_stop_id`. Never place a plain sell-limit at entry: below the market it executes immediately. The pre-#175 synthetic stop (cancel take, then sell-limit at the observed price) survives only as a fallback while `broker_stop_id` is NULL.
- Trailing is an amend by duplication (Product Owner decision of 2026-09-22): `PostStopOrder` (new, higher stop) -> `GetStopOrders` (confirm it is active) -> `CancelStopOrder` (the older, lower one), so the position is never unprotected. When confirmation is inconclusive the old stop is removed later by `_cancel_pending_stops()` — on the next confirmation or after `oco_check_delay_seconds` — so two active stops never survive the grace period.
- Every physical broker attempt, including SDK retries and account discovery, shares one token bucket (`api_rate_limit`, maximum 10/sec) with priority classes (Issue #175): `protection` > `trailing` > `entry`; entry calls must leave `entry_token_reserve` tokens for protection. Do not add independent broker calls outside `_broker_call` or bypass the client's `before_request` hook.
- Exits are reconciled with real fills: when the broker portfolio no longer shows the position, `get_operations()` supplies `exit_price_actual` and `lots_executed` (shares converted with `lot_size`), the reason comes from the authoritative stop status (`EXECUTED` -> `closed_stop`/`closed_trailing`, still `ACTIVE` -> `closed_take`), and `slippage_bp` / `slippage_r` are written next to the model price.
- OCO is emulated by monitoring: after each close both legs are queued and re-checked after `oco_check_delay_seconds` through `GetStopOrders` / `GetOrders`. A surviving leg is cancelled manually with a critical `OCO monitoring:` alert, retried up to `oco_check_attempts` times.
- Protection invariants are verified every `broker_stop_verify_interval_seconds`: an open position without `broker_stop_id`, or with an id the broker no longer lists as active, logs a critical `Invariant violation:` (arming failures log `protection_failed`) and is re-armed with exponential backoff from `protection_retry_seconds`.
- SIGTERM/SIGINT requests cleanup. Pending entry orders are always cancelled, stops go through `cancel_stop_order()`; open holdings are flattened only when `close_positions_on_shutdown=true`. With the default false value, holdings remain open and keep their broker-side protection (Issue #174).
- Orphan stop sweep (Issue #199, §46.12): every `orphan_stop_sweep_interval_seconds` the executor compares `GetStopOrders(active)` with `live_positions` and cancels ACTIVE SELL stops that no row references, that sit inside the configured universe, that neither the OCO nor an amend pass claims, that the broker holds no position for and that are older than `orphan_stop_grace_seconds`. Two consecutive sweeps must agree, at most `orphan_stop_max_cancels` stops are cancelled per pass and exceeding that cap makes the pass cancel nothing (`orphan_sweep_fail_closed_total`, throttled critical alert). Keeps are logged `orphan_stop_kept ... reason=<code>` at DEBUG, cancellations `orphan_stop_cancelled stop_order_id=... reason=orphaned`, counters are under `protection.*` of `/api/live-trading/metrics`.

- Read rejected BUY diagnostics in `reports/live-executor/executor.log`. Each `Live signal skipped` record contains `ticker=<ticker>`, a stable `reason=<code>`, and relevant values. Expected filter/capacity codes are `outside_entry_window`, `stale_or_missing_orderbook`, `imbalance_below_threshold`, `insufficient_cash`, `invalid_stop`, `insufficient_capital`, `max_open_positions`, and `broker_error`; `min_lot` remains executable under the sizing contract. For example, `reason=imbalance_below_threshold imbalance=0.9 imbalance_threshold=1.0` means the stream is live but the filter rejected entry, while `reason=stale_or_missing_orderbook orderbook_age_seconds=missing` indicates absent book data. These records are emitted only after `StrategyEvaluator` produces a BUY decision; no skip records can simply mean that no BUY signal was generated. Broker errors log only the operation and exception type, never credentials, account details, or exception text.
- Diagnostics: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Tests: `cd backend && python -m pytest -q tests/test_live_executor.py tests/test_moex_session.py`.

## 16. Operating Telegram Paper Alerts

- Entry point: `app.notifications.telegram_notifier.TelegramNotifier`; paper-trading integration lives in `paper_trader.py`.
- Set `TGM_TOKEN` and `TGM_CHAT`; legacy `TGM_CHAT_ID` remains a fallback. `TGM_APP_ID` and `TGM_APP_HASH` are loaded but not used by the Bot API. Never print or commit these values.
- The notifier sends Markdown open/close messages with ticker, BUY/SELL, price, lot and unit counts, PnL, and reason. Stop/take use distinct icons; critical events use 🚨.
- Calls are serialized at one attempt per second. Delivery errors are warnings only and must never terminate paper trading.
- Large-drawdown alerts use `risk.max_daily_loss_pct` and fire only on threshold crossing. GAME OVER fires only on the first transition to non-positive equity.
- Tests: `cd backend && python -m pytest -q tests/test_telegram_notifier.py tests/test_paper_trader_notifications.py`.

## 17. Operating the Live Trading Panel

- Open the `Live Trading` frontend tab. It polls sandbox data from `trading.live_positions`, PnL dynamics, and Telegram status every 10 seconds.
- Open-position `current_price` comes from the latest order-book best bid, with best ask as fallback. Missing market data is rendered as unavailable rather than using a stale hardcoded price.
- Both tables use the shared `DataTable` and filter chips used by Strategy Lab. Filters open from column headers, and date ranges use the shared calendar `DatePicker`. History retains server-side sorting and pagination; no exact status filter means all closed positions (`closed_stop` and `closed_take`).
- `/api/notifications/status` performs Telegram `getMe` without sending a message and caches the result for 30 seconds. `configured=false` means `TGM_TOKEN` or chat ID is absent; `configured=true` with `disconnected` means the Bot API probe failed.
- Frontend check: `cd frontend && npm run build`. Backend checks: `cd backend && python -m pytest -q tests/test_live_trading_api.py tests/test_notifications_api.py tests/test_telegram_notifier.py`.

## 18. Operating the Live Trading Universe

- Paper ranking stays `get_trading_universe()` (top-15 from `trading.trading_universe`). Do not shrink that table.
- Streaming and data refresh use `get_streaming_universe()` = top-15 ∪ `LIVE_UNIVERSE`.
- Sandbox execution uses `LIVE_UNIVERSE` / `get_live_trading_universe()`: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT, FEES, GAZP, PLZL (Issue #135 PO list plus 2026-09-02). The getter does **not** clip names that sit outside the paper top-15. `LiveExecutor.initialize()` intersects paper-strategy tickers with this list.
- Historical Issue #66 ranking (SBER, LKOH, RUAL, NVTK, GAZP) lives in `analytics/issue-66-live-universe/`. Do not rewrite that package to match the current PO list.
- Locked paper name for preflight is `EXPECTED_LOCKED_STRATEGY` = `test_20260830_new_level`.
- Tests: `cd backend && python -m pytest -q tests/test_trading_config.py tests/test_live_universe_analysis.py tests/test_live_executor.py`.

## 19. First Sandbox LiveExecutor Canary

Use a 60-120 minute window during the MOEX session, preferably 10:00-16:00 MSK. This verifies the execution chain; it is not a performance test. Never use the historical `DURATION_MINUTES=1200` default for the canary.

1. Rebuild after merging the live-universe and refusal-logging changes: `docker compose up -d --build backend`. Confirm `http://localhost:8000/health` returns `status=ok`.
2. Keep exactly one each of `data_refresher`, `online_data`, `live_engine`, and `paper_trader` running. Do not stop paper trading for the canary. If they are not running, start the normal paper stack first with an explicit duration that covers the canary window.
3. Run the read-only preflight immediately before the executor:
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   It fails unless the backend is healthy, `LIVE_UNIVERSE` is exactly SBER/LKOH/RUAL/NVTK/GAZP, the only locked paper strategy is `test_20260731`, free sandbox RUB is positive, all five books are at most five minutes old, every paper process has exactly one instance, `trading.trading_universe` still has 15 rows, and `allow_real_trading=false`.
4. Start a one-hour executor without restarting the paper processes:
   `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 DURATION_MINUTES=60 ./start_processes.sh`.
   `PRESERVE_PAPER_PROCESSES=1` aborts unless all four paper processes are already running exactly once.
5. Within five minutes, `reports/live-executor/executor.log` must contain `Sandbox LiveExecutor started` with the initialized ticker count. A refusal line appears only after a BUY decision; no refusal lines can mean that `StrategyEvaluator` produced no BUY. Watch the Live Trading tab and `trading.live_positions` as well.
6. The executor stops automatically at the duration limit. To stop it early, send SIGTERM only to the process whose command contains `LiveExecutor`; do not rerun the full paper startup. With `close_positions_on_shutdown=false`, sandbox holdings remain open, while pending/protection orders are cancelled and their protection IDs are cleared from the DB; inspect remaining holdings immediately.
7. Give the paper stack a duration that covers the canary plus a margin. If a paper process expires during the run, restart only the four paper workers; do not rerun `start_processes.sh`, because Step 0 would kill `LiveExecutor`.

Useful read-only SQL:

```sql
SELECT id, name, in_paper_test, locked
FROM trading.strategies
WHERE in_paper_test=true AND locked=true;

SELECT ticker, max(timestamp) AS latest_orderbook
FROM trading.online_orderbook_aggregates
WHERE ticker IN ('SBER','LKOH','RUAL','NVTK','GAZP')
GROUP BY ticker ORDER BY ticker;

SELECT count(*) AS universe_size
FROM trading.trading_universe;

SELECT timestamp, equity_rub
FROM trading.paper_equity
ORDER BY timestamp DESC LIMIT 2;
```

After the run, record in Issue #74: initialized tickers from the startup log; refusal counts grouped by `reason`; executed BUY count; the latest positions from the query below; and evidence that `paper_equity` advanced during the same period.

```sql
SELECT ticker, status, size_lots, entry_price, broker_order_id
FROM trading.live_positions
ORDER BY id DESC LIMIT 20;
```

Do not modify the locked strategy, RR, imbalance threshold, or `trading.trading_universe`. Never set `allow_real_trading=true`; this run is sandbox-only.

## 20. Operating SignalEngine Strategy Lab filters

- Entry points: `app.analytics.signal_pattern_filters` (inline evaluate + last-closed HTF) and `StrategyEvaluator.check_entry`. Context is built by `build_strategy_context`.
- Path rule: `signal_4h_buy` looks up `trading.signals`; the ten SignalEngine ids call `BasePattern.evaluate` on `trading.indicators`. Do not mix a `pattern_name` lookup into the SignalEngine path. Do not replace `MR_RSI_Reversal` with `rsi_oversold`.
- `timeframe` contract: `SIGNAL_PATTERN_TIMEFRAME_PARAM` in `pattern_registry.py` (select, options 30min/1h/2h/4h/1d/1w, default 4h). Full schemas are in `SIGNAL_ENGINE_PATTERN_SCHEMAS`; 4h defaults match current SignalEngine `get_thresholds` / PA `evaluate` literals. `normalize_patterns` fills them; evaluator still keys inline evaluate by `timeframe` only.
- How to enable in the constructor: add a SignalEngine chip from `GET /api/patterns` (do not hardcode the ten ids in `StrategyLab.tsx`). Chips are grouped by API `category` with RU titles. Timeframe and pattern params are set in `PatternSettingsModal` (no extra global TF selector). Save runs `normalize_patterns`; the same config is used by `strategy_backtest`, paper (`get_active_paper_strategy` → `StrategyEvaluator`), and live. Do not overwrite locked `test_20260731`. The two-chip fallback (`levels_reversal` + `signal_4h_buy`) is only used when the patterns API is empty; a live registry is never replaced by that list.
- Filter uses the last closed HTF bar. Missing indicator rows reject the entry. `2h` is in the contract but is not persisted by the current aggregator/indicator pipeline.
- Locked paper strategy `test_20260731` must stay `levels_reversal` + `signal_4h_buy` only.
- Unit tests: `cd backend && python -m pytest -q tests/test_signal_engine_filters.py tests/test_pattern_registry.py tests/test_signal_pattern_e2e.py`.

## 21. Operating Pattern Chart Preview (Epic #87)

- Entry point: `POST /api/patterns/preview` in `strategy_jobs.py`; logic in `app.analytics.pattern_preview`.
- Request: `ticker`, `pattern_id`, draft `params`, `date_from`, `date_to`. Timeframe comes from params (`level_timeframe` for `levels_reversal`, `timeframe` for SignalEngine ids).
- Response: `status` (`ok` / `empty` / `error` / `unsupported`), `candles`, typed `overlays` (`ray`, `band`, `line`, `marker`). Issue #88 implements `levels_reversal`: all levels with `defined_ts` in the window; each level emits a `ray` from `defined_ts` to the last visible bar plus a `band` for the ATR zone. Do not emulate rays with infinite price lines.
- Unknown `pattern_id` returns `status=error` without 500. Missing candles (including unsupported `2h`) returns `status=empty` with a clear message.
- Other pattern ids return `status=unsupported` with candles only until #91 adds overlay renderers. Frontend chart work is #89–#92.
- Unit test: `cd backend && python -m pytest -q tests/test_pattern_preview.py`.

## 22. Operating the Levels State Machine

- Entry points: `LevelsTracker` / `get_levels_with_state` / `is_broken` in `levels_engine.py`. Initialise from `get_levels()` (`build_levels` alias). Feed bars of the **same** timeframe as the levels (typically 4h). In-memory only — no table, no migration.
- Thresholds come only from `LEVEL_STATE_MACHINE` in `trading_config.py`: `breakout_buffer_atr=0.25`, `confirm_bars=2`, `min_penetration_atr=0.5`, `zone_extension_atr=0.5`. `zone_extension_atr` documents the current `build_levels` zone width; the tracker does not recompute `zone_lower`/`zone_upper`.
- Resistance break: last `confirm_bars` closes all above `zone_upper`, last close above `zone_upper + buffer×ATR`, and max(window) at least `zone_upper + min_penetration×ATR`. Support is symmetric below `zone_lower`. First close back inside the native zone after a break flips `broken_up → flipped_support` / `broken_down → flipped_resistance`. Failed breakouts are not reverted to `active` in this iteration.
- `overlapping_resistance_zone_at` vetoes only `active` resistances when a `state` column exists, and skips `tracker.is_broken(level_id)` when a tracker is passed (Issue #107). Pass a tracker snapshot taken **after** `update()` through closed HTF bars only. Frames without `state` and callers that omit `tracker` keep the Issue #97 behaviour.
- `StrategyEvaluator` constructs `LevelsTracker` when `level_breakout_retest`, `levels_sr_breakout`, or `levels_sr_support` is in `config.patterns`. Locked `test_20260731` enables none of them. `bars_since_breakout(level_id)` counts HTF bars since the confirmed break.
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_state_machine.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py`.

## 23. Operating the Level Breakout Retest Pattern

- Entry points: `check_breakout_retest` / `evaluate_level_breakout_retest` in `patterns/level_breakout_retest.py`; AND-filter `_check_level_breakout_retest` in `StrategyEvaluator`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/` (that would shadow `breakout.py`).
- Lab schema: `PATTERN_REGISTRY['level_breakout_retest']` (also copied onto `SIGNAL_ENGINE_PATTERN_SCHEMAS` for the Issue #107 AC / `GET /api/patterns`). Defaults: `level_timeframe=4h`, `retest_window_bars=20`, `retest_zone_atr=0.5`, `entry_trigger_bullish=true`, `stop_atr=1.0`, `risk_reward=2.0`. The bullish-body ratio `0.6` is not Lab-tunable; it lives in `LEVEL_BREAKOUT_RETEST` in `trading_config.py`.
- Criteria (all must hold): tracker state `broken_up` or `flipped_support`; close in `[level ± retest_zone_atr×ATR]`; close ≥ broken `level_price`; `bars_since_breakout <= retest_window_bars`; if `entry_trigger_bullish`, `close > prev_high` OR bullish body.
- Stop/take: `stop = entry − stop_atr×ATR`, `take = entry + risk_reward×(entry−stop)`. When the pattern is enabled these replace levels stop/take; the top-level config RR filter is not applied on top (pattern RR already encodes the ratio).
- Context: `build_strategy_context` returns `htf_bars` (same TF as levels). The evaluator feeds only HTF bars whose close ≤ current 1min ts (no lookahead). Paper/live `load_context` / `update_context` pass this frame through. Lab/plugin path: `portfolio_backtest` puts the same frame on `MarketContext.htf_bars` (Issue #116); do not rely on `candles_4h`.
- Veto interaction: with the pattern on, a broken resistance is no longer an opposing zone (`is_broken`). Without the pattern, every overlapping resistance still vetoes (locked `test_20260731`).
- Composability: AND with `levels_reversal` (still required for the support-zone path) and with SignalEngine / `signal_4h_buy` filters. Lab chip: handover §24. `GET /api/patterns` is the source of names, hints, icon, and param schema.
- Do not rewrite locked `test_20260731`.
- Unit tests: `cd backend && python -m pytest -q tests/test_level_breakout_retest.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py`.

## 24. Operating the Level Breakout Retest Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts`, `patternValidation.ts`.
- Enable from the **Пробой** group. Visible name is API `label` («Пробой уровня с ретестом»); EN `label_en` («Level Breakout Retest») is in the tooltip and under the modal title. Icon `breakout_up` (arrow through a level) is also from the API.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the six params in the frontend. Schema: `level_timeframe` (1h/4h/1d), `retest_window_bars` (1–100), `retest_zone_atr` (0.1–2.0), `entry_trigger_bullish`, `stop_atr` (0.5–3.0), `risk_reward` (≥1). Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Combine with `levels_reversal` (still required for the support-zone path) and optional SignalEngine / `signal_4h_buy` filters. AND logic is unchanged. Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: after a confirmed resistance break you want a retest entry (role reversal) instead of (or in addition to) a native support-zone entry. Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- This AND-filter is **not** a substitute for `levels_sr_breakout` (handover §25). Epic #115 isolates the composite; do not combine the two chips as “the new strategy”.

## 25. Operating the Composite S/R Pattern (`levels_sr_breakout`)

- Entry points: `PATTERN_ID` / sources in `patterns/levels_sr_breakout.py`; OR logic in `StrategyEvaluator._check_sr_breakout_entry`. Path B reuses `check_breakout_retest`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/`.
- Lab schema: `PATTERN_REGISTRY['levels_sr_breakout']` (also on `SIGNAL_ENGINE_PATTERN_SCHEMAS` for `GET /api/patterns`). Category `levels` (next to `levels_reversal`, not in breakout). Icon `support_breakout` (must stay distinct from `breakout_up`). Params = all `levels_reversal` fields + retest fields (`retest_window_bars`, `retest_zone_atr`, `entry_trigger_bullish`, `stop_atr`, `risk_reward`). Lab chip: handover §26. Do not hardcode the param keys in TSX.
- Isolated run: `config.patterns` contains `levels_sr_breakout` and optionally `signal_4h_buy` / SignalEngine ids. `levels_reversal` is **not** required. `run_strategy_backtest` treats the composite as a sufficient entry engine.
- Order in `check_entry` after session / HTF / `_sync_tracker`: (1) common AND (`signal_4h_buy`, SignalEngine, 1min indicator filters); (2) path B — `check_breakout_retest` → `source=levels_sr_breakout_resistance`, ATR stop/take, no second config RR filter; (3) else path A — support zone + confirm + veto of *active* resistance with tracker (`source=levels_sr_breakout_support`, levels stop/take, top-level RR filter). If both would fire, path B wins.
- Both chips present (`levels_reversal` + `levels_sr_breakout`): composite wins — one support path, no doubling.
- Do not AND with `level_breakout_retest` as a replacement for this engine. The Epic #105 AND-filter contract stays unchanged.
- Tracker / `htf_bars`: same feed as #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit tests do not depend on the Lab UI.
- Locked `test_20260731` must not enable this id (paper/live veto and levels stop/take stay bit-for-bit).
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.

## 26. Operating the Composite S/R Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts` (`resolveConfirmWindows`), `patternValidation.ts`. Icon map: `PatternIcon.tsx` keyed by API `icon`, not by pattern id.
- Enable from the **Уровни** group (not **Пробой**). Visible name is API `label` («Поддержка + пробой сопротивления»); EN `label_en` («Support Reversal + Resistance Breakout») is in the tooltip and under the modal title. Icon `support_breakout` (support line + resistance break) is also from the API and must stay distinct from `breakout_up`.
- This chip **replaces** `levels_reversal` for the new strategy. Isolated run: turn on `levels_sr_breakout` and optionally `signal_4h_buy` / SignalEngine; leave `levels_reversal` and `level_breakout_retest` off. If both levels chips are on, the backend composite wins (one support path) — do not treat that as a third AND.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the param list in the frontend. Schema = all `levels_reversal` fields + retest fields. Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Top-level `config.confirm_windows` is taken from the enabled schema that owns that param; the composite wins over `levels_reversal` (same as backend `_LEVELS_CONFIRM_PATTERN_IDS`). Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: you want one Lab engine that enters on native support **or** on a confirmed resistance retest. Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated AFKS smoke (Issue #119): handover §27. Lab-universe A/B (Issue #124): handover §28. Support-only engine (Issue #127): handover §29. Lab chip (Issue #128): handover §30. Isolated support universe (Issue #129): handover §31. Portfolio 50k (Issue #130): handover §32. Do not treat either package as a paper verdict.

## 27. Operating the AFKS composite smoke

- Package: `analytics/issue-119-afks-sr-breakout-smoke/`. Isolated ticker, not 50k slots.
- A = `levels_reversal` + `signal_4h_buy` (same geometry as #103). B = only `levels_sr_breakout` + `signal_4h_buy`. Period `2024-08-01` … `timestamp < 2026-08-21`.
- Primary engine is `run_strategy_backtest` so trades keep `source`. The Lab plugin path matches n/PF after #116 but currently drops `source` from plugin trades.
- B-support can exceed A: the composite passes `LevelsTracker` into the veto, so a broken resistance no longer blocks a support entry.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without DB: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py`. Full re-run: `python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py`.
- Units: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.

## 28. Operating the Lab-universe composite A/B

- Package: `analytics/issue-124-sr-breakout-universe/`. Isolated 28-ticker Lab universe (`get_big_tickers`), not live top-5 and not `run_params.tickers`.
- A/B configs are the published #119 SHA pair. Period `2024-08-01` … `timestamp < 2026-08-21`. Engine is `run_strategy_backtest` so trades keep `source`.
- Isolated B is larger than A for two reasons: path B (`levels_sr_breakout_resistance`) plus extra support after the tracker-aware veto. Do not treat B-support n as a bit-for-bit copy of A.
- Optional 50k / 10k / max-5 replay of B candidates is a **separate** block. Do not mix that PF/equity with isolated ticker PF. It is not a paper verdict.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without a new backtest: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Full re-run: `python analytics/issue-124-sr-breakout-universe/extract_inputs.py` (resumable).
- Units: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.

## 29. Operating the Support-with-tracker Pattern (`levels_sr_support`)

- Entry points: `PATTERN_ID` / `SOURCE` in `patterns/levels_sr_support.py`; support-only path in `StrategyEvaluator._check_sr_support_entry`. Not a SignalEngine `BasePattern` — do not add the id to `SIGNAL_ENGINE_PATTERN_IDS`. Do not put the file under `patterns/breakout/`.
- Lab schema: `PATTERN_REGISTRY['levels_sr_support']` (also on `SIGNAL_ENGINE_PATTERN_SCHEMAS` for `GET /api/patterns`). Category `levels` (next to `levels_reversal` and `levels_sr_breakout`, not in breakout). Icon `support_tracker` (must stay distinct from `breakout_up` and `support_breakout`). Params = **only** `levels_reversal` fields — no retest keys. Lab chip: handover §30. Do not hardcode the param keys in TSX.
- Isolated run: `config.patterns` contains `levels_sr_support` and optionally `signal_4h_buy` / SignalEngine ids. `levels_reversal` is **not** required. `run_strategy_backtest` treats this id as a sufficient entry engine.
- Order in `check_entry` after session / HTF / `_sync_tracker`: (1) if `levels_sr_breakout` is on, the composite wins (unchanged); (2) else if `levels_sr_support` is on — common AND, then support zone + confirm + veto of *active* resistance with `tracker=self._tracker` → `source=levels_sr_support`, levels stop/take, top-level RR filter. **Do not** call `check_breakout_retest` on this id.
- Both chips (`levels_sr_support` + `levels_sr_breakout`): composite wins. `levels_sr_support` + `levels_reversal`: the new id wins (one support path, no doubling). `_LEVELS_CONFIRM_PATTERN_IDS` order is composite > support-with-tracker > `levels_reversal`.
- Difference vs `levels_reversal`: tracker is passed into the veto, so a broken resistance no longer blocks a valid support entry. Difference vs `levels_sr_breakout`: no path B / retest.
- Tracker / `htf_bars`: same feed as #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit tests do not depend on the Lab UI.
- Locked `test_20260731` must not enable this id (paper/live veto and levels stop/take stay bit-for-bit).
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_levels_sr_breakout.py tests/test_strategy_plugin.py`.

## 30. Operating the Support-with-tracker Lab chip

- Entry points: `StrategyLab.tsx` (chips grouped by API `category`) and `PatternSettingsModal.tsx` (fields from `PatternDef.params`). Helpers: `patternLab.ts` (`resolveConfirmWindows`, `LEVELS_CONFIRM_PATTERN_IDS`), `patternValidation.ts`. Icon map: `PatternIcon.tsx` keyed by API `icon`, not by pattern id.
- Enable from the **Уровни** group (not **Пробой**). Visible name is API `label` («Поддержка с трекером»); EN `label_en` («Support Reversal (tracker veto)») is in the tooltip and under the modal title. Icon `support_tracker` (support line + tracker watching the zone, no breakout arrow) is also from the API and must stay distinct from `support_breakout` and `breakout_up`.
- This chip **replaces** `levels_reversal` / `levels_sr_breakout` for this strategy. Isolated run: turn on `levels_sr_support` and optionally `signal_4h_buy` / SignalEngine; leave `levels_reversal`, `levels_sr_breakout`, and `level_breakout_retest` off. If `levels_sr_breakout` is also on, the backend composite wins. If `levels_sr_support` and `levels_reversal` are both on, the new id wins (one support path) — do not treat either mix as a third AND.
- Click the chip label to open settings (enables the pattern and prefills schema defaults). The checkbox toggles; turning a parameterized chip on also opens the modal. Gear still opens settings.
- Do not hardcode the param list in the frontend. Schema = `levels_reversal` fields only — no retest keys. Out-of-range values get a red border + message; Apply and «Сохранить и запустить» are blocked. «Сбросить дефолты» restores `schema.default`. «Отмена» / Esc discards the draft.
- Top-level `config.confirm_windows` is taken from the enabled schema that owns that param; priority is composite > support-with-tracker > `levels_reversal` (same as backend `_LEVELS_CONFIRM_PATTERN_IDS`). Save goes through existing `POST /api/strategies` then `POST /api/strategies/{id}/run` with `config.patterns` as `{ id: params }` — not `POST /api/backtest`.
- When to enable: you want the #124 B-support path (tracker-aware veto, no resistance retest). Keep it off on locked `test_20260731` (the Lab row stays read-only).
- There is no `frontend` service in `docker-compose.yml`. Check locally: `cd frontend && npm test && npm run build`. Backend schema: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated vs #124 B-support is Issue #129 (handover §31). Portfolio 50k is Issue #130 (handover §32). Do not treat this chip as a paper verdict.

## 31. Operating the isolated support-with-tracker universe

- Package: `analytics/issue-129-sr-support-universe/`. Isolated 28-ticker Lab universe (`get_big_tickers`), same period as #124. Not live top-5 and not `run_params.tickers`.
- C = only `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Engine is `run_strategy_backtest` so trades keep `source=levels_sr_support`.
- Exclusive B-support in #124 is a **composite label** (path B steals dual bars and occupies the single slot). Isolated C is the **runnable** support-only book: n=4380 PF 1.45. Do not treat exclusive 3811 / 1.51 as bit-for-bit C.
- Extra 611 vs exclusive: 610 occupancy (C enters while the composite is in a path-B or other trade), leftover 1 (PHOR `2026-08-14 14:48`). Missing 42: cascade (a C extra occupies the slot so a later B-support trade cannot fire). Extra PF 0.95 — isolated extras are worse than exclusive 1.51.
- AFKS: C 89 / 1.49; exclusive 78 ⊆ C; not mix 116 / 1.46. ALRS `2026-08-20 11:50:24` @ 19.80 blocked. Resistance-source n=0.
- Issue #130 must use C (4380 / 1.45), not exclusive 3811 / 1.51. Do not mix isolated PF with a 50k portfolio. Portfolio package: handover §32.
- Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Replay without a new backtest: `python analytics/issue-129-sr-support-universe/analysis.py`. Full re-run: `python analytics/issue-129-sr-support-universe/extract_inputs.py` (resumable).
- Units: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.

## 32. Operating the support-with-tracker portfolio

- Package: `analytics/issue-130-sr-support-portfolio/`. Slot replay of isolated C from #129, same 28 names / volume-order as #103/#44. Not live top-5.
- C = only `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Candidates come from published #129 `results.json` via `run_strategy_backtest` (`source=levels_sr_support`). Do not filter #124 B-mix by `source`.
- Slots: 50,000 RUB / 10,000 / max 5. Period `2024-08-01` … `timestamp < 2026-08-21`. Daily equity is realized closes, no mark-to-market.
- Published C book: n=3237 PF 1.33 equity 96,204.63 daily Max DD 6.08% event Max DD 6.98% skipped 1143 no GAME OVER. Isolated C remains 4380 / 1.45 — do not mix those PF numbers.
- Comparison (other books, not substitutes): #44 equity 96,343.49 n=3500 PF 1.31; #103 equity 89,055.31 n=2070 PF 1.34; #124 B-mix equity 98,432.94 n=2837 PF 1.32 (support+resistance candidates).
- ALRS `2026-08-20 11:50:24` @ 19.80 is absent from candidates and portfolio entries. Resistance-source n=0.
- Verdict: not paper (default without an explicit PO decision). Do not lock/paper-flag or overwrite `test_20260731`, `test_20260820`, `test_20260821`. Lab draft if needed: `test_YYYYMMDD_sr_support`.
- Replay without a new backtest: `python analytics/issue-130-sr-support-portfolio/analysis.py`. Slot JSON: `python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source 129`. Notebook: `python analytics/issue-130-sr-support-portfolio/build_notebook.py --execute`.
- Units: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.

## 33. Operating sandbox LiveExecutor on `test_20260830_new_level` (Issue #135)

PO override of the #130 «not paper» verdict for a **different** Lab row: `test_20260830_new_level` (`levels_sr_support` + `signal_4h_buy`, RR 1:3). Not the published C book (RR 1:2). Not Issue #77 (`test_20260731` + #66 top-5).

1. Confirm exactly one locked paper strategy: `test_20260830_new_level`. Keep `test_20260731` unlocked; do not rewrite its config. An open FEES paper position on the old name stays under monitor.
2. Rebuild: `docker compose up -d --build backend`. `/health` must be `ok`.
3. Paper stack must cover the Monday session with margin. Do not stop paper for live. Streaming/refresh covers top-15 ∪ LIVE_UNIVERSE. Do not shrink `trading.trading_universe`.
4. Preflight in the MOEX session (books will be stale on Sunday):
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   It fails unless backend is healthy, `LIVE_UNIVERSE` is the 12 PO names, the only locked strategy is `test_20260830_new_level`, free sandbox RUB > 0, all 12 books are ≤5 minutes old, each paper process has exactly one instance, the DB universe still has 15 rows, and `allow_real_trading=false`.
5. Overnight sandbox day (Issue #137), after backend rebuild:
   `START_LIVE_EXECUTOR=1 ./start_processes.sh`
   Do not set `DURATION_MINUTES`. Launch Sunday evening; LiveExecutor waits until Monday 10:00 MSK. If paper is already running and covers Monday 19:00: `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 ./start_processes.sh`.
6. Leftover canary RUAL is already `closed_stop`. No open sandbox holdings at the #135 start.
7. After the window, record in Issue #135 / #137: init tickers, `reason=` counts, BUY count, latest `live_positions`, and evidence that `paper_equity` advanced. Never set `allow_real_trading=true`.
   Historical canary with a fixed window remains `DURATION_MINUTES=60` (handover §19).


## 34. Stepped trailing-stop analytics on `test_20260830_new_level` (Issue #139)

- Analytics-only A/B: baseline fixed stop/take 1:3 (**A**) vs the same entry with a
  stepped trailing stop (**B**) in the portfolio simulator (50k / 10k / max 5 / volume
  priority / GAME OVER). Single difference = the exit rule; entry, initial 1R risk,
  commission and the 28-name `run_params.tickers` universe are identical. Full period
  `2024-08-01` … `timestamp < 2026-08-21` (not the express window).
- Package: `analytics/issue-139-trailing-stop-new-level/`. The trailing lives ONLY in
  `trailing.py` as a configurable stepped exit (list of `{"trigger": <R>, "stop": <R>}`,
  default `+2R→+1.5R`, `+2.5R→+2R`). Never wire it into `StrategyEvaluator`,
  `portfolio_simulator.py`, paper or sandbox. Steps are in R from the entry, not % hardcode.
- Fill model mirrors `StrategyEvaluator.on_bar`: within a bar check stop (`low<=stop`)
  before take (`high>=take`); a step armed by the current bar's high raises the stop for
  the NEXT bar only (no intra-bar look-ahead). A trailing exit is never later than the
  baseline exit (the take is unchanged and the ratchet only tightens the stop).
- `extract_inputs.py` drives the unified brain over 1min candles (identical entries to the
  production backtest) and evaluates BOTH exits on the same intra-trade path; it never
  writes trades to the DB and checks the four protected rows (126 / 36 / 102 / 118) before
  and after. `baseline_replay_mismatches` must be 0 (proves the replayed A equals the engine).
  Paths stay in memory; `results.json` stores only the compact per-trade A and B outcomes, so
  `analysis.py` runs without a DB.
- Note #129 / #130 used RR 1:2 (SHA `3b7864c4…aedb1b`); #139 targets the locked
  `test_20260830_new_level` id=126 (RR 1:3, SHA `dfc855195ade…`). Do not reuse the #129
  candidate book; #139 re-extracts from the DB.
- Run: `python analytics/issue-139-trailing-stop-new-level/extract_inputs.py --workers 4`
  then `python analytics/issue-139-trailing-stop-new-level/analysis.py`. Extract is
  resumable (per-ticker cache under `reports/Vulpec/139_trailing-stop-new-level/cache/`).
- Verdict and exact A/B numbers live in `summary.json` / `report.md` (RU+EN). Headline:
  B trailing 103,176.00 RUB vs A baseline 95,180.01 RUB (Δ +7,995.99 RUB, +8.40%); PF
  1.41→1.54, daily Max DD 6.49%→2.74%, win rate 24.2%→42.3%, no GAME OVER in either book;
  trailing closes 36.9% of B (take 6.6%, initial stop 56.5%), candidate trades 3305 of which
  1578 reached +2R, `baseline_replay_mismatches=0`. Verdict: trailing improves capital →
  consider adopting (analytics only; the production exit path is untouched, not a paper lock).
- Units (no DB): `cd backend && python -m pytest -q tests/test_issue139_analysis.py`.

## 35. Trailing-grid robustness analytics on `test_20260830_new_level` (Issue #143, lattice v3 by #155)

- Robustness lattice over the #139 book: same 28 tickers, config id=126, `StrategyEvaluator`,
  slot simulator and `apply_trailing`; only the stepped-trailing grids and the stress factors
  (commission, slippage) change. Simulation (paper mode) — no parity with the engine trailing
  path and no `trailing_grid_id` column yet.
- Package: `analytics/issue-143-trailing-robustness/` — `run.py`, `grids.json`, `README.md` plus
  the published run artifacts (`report.md`, `run.md`, `summary.json`, `report.json.gz`,
  `grids.csv`, `walkforward.csv`, `contract.json`, `exits.jsonl.gz`, `extract_summary.json`).
  Artifacts live next to the code (the `analytics/` convention); `.gitignore` excludes
  `analytics/*/cache/` (28 gzipped 1m paths, fully re-extractable) and `analytics/*/out_*/`
  (debug runs). Run logs go to `reports/Vulpec/143_trailing-robustness/` (ignored), as the
  issue text requires.
- Run from the repo root: `python analytics/issue-143-trailing-robustness/run.py --stage all`
  (extract → analyze → report, ≈2 min on a warm cache). Text-only report refresh without DB:
  `--stage report`. Debug slice: `--tickers SBER --limit 20 --out-dir out_debug` — keep
  `--out-dir` at the repo root; pointing it inside the package leaves untracked duplicates
  (`analytics/issue-143-*/reports/run.md`) that must not be committed.
- Headline of the published run (`grids.json` schema `143-trailing-v3`): 8 grids — six multi-step
  (2–4 steps, incl. the PO probe `ultra_late_tight`) and two single-step (`single_step_2_15`,
  break-even `breakeven_2_0`) — over the same 3 305 candidate trades; parity with #139 confirmed
  (0 exit-mechanic mismatches; equity 103 216 ₽ vs 103 176 ₽ in #139, PF 1.55, DD 2.72 pp).
  Equity spread — 14 607 ₽ (`ultra_late_tight` 110 434 ₽ best, `three_step_steady` 95 827 ₽ worst);
  `ref139` tops the composite stability score (47.3/100) and all eight grids stay profitable in 9/9
  walk-forward windows. Max stress (0.15 % commission + 20 b.p. slippage, 96 runs) drops the worst
  grid to 9 614 ₽ with no game-over; worst grid against the control (stakeholder test) is
  `three_step_steady` (Δequity −525 ₽). Exit concordance: identical outcome for 69.3 % of trades
  (median pairwise Spearman ρ 0.89, min 0.7497), exit reason flips vs the base grid in 1 797 of
  3 305 trades (1 726 material at the 20 ₽ threshold). See report §4–§8.
- Machine-readable lattice slice (Issue #155): `summary.json.lattice` — `groups` by step count,
  `pairs` (single step ↔ the ladder sharing its first step), `boundaries` (PO probe / single-step /
  break-even), `verdicts`, and both thresholds: `material_rub_per_trade` (50 RUB, #143 lattice-wide)
  vs `po_material_rub_per_trade` (20 RUB, the #155 stakeholder threshold that judges the probe).
  Validation is tracked, not scratch: `cd backend && python -m pytest -q
  tests/test_issue155_analysis.py` (canonical artifacts + synthetic `run.lattice_analysis`).
- Reproducibility: `--stage report` rebuilds `report.md`, `summary.json` and `run.md` from the committed
  `report.json.gz` alone — verified in a clean worktree (no `cache/`, no DB): `report.md` and
  `summary.json` come out byte-identical, `report.json.gz` matches on every value (only the gzip header
  timestamp differs), and `run.md` differs only as a protocol log (timestamp, stage, elapsed). Full
  `--stage all` of the published v3 run: exit 0 — 231.9 s of analysis (`summary.json.elapsed_sec`).
- Documented limits (report §13, keep them honest): the grid-shape debt is closed by #155 (8 grids,
  single-step and break-even bounds included); still open — milder stress than specified (0.06/0.10/0.15 %
  commission and b.p. slippage instead of 0.3/0.6/1.5 % and MOEX price steps, no min-lot sensitivity),
  no `risk_reward` sensitivity, no fixed-stop-vs-trailing threshold at max stress (book A was never
  stress-run), only one direct single-step ↔ ladder pair, and no charts. These stay in the report's
  continuation block — do not silently extend the lattice inside this package.
- Verdict: analytics only. Do not read the composite robustness score as an objective: in this run its
  stress-capital, DD-degradation and walk-forward components tie every grid (0.0 / 0.0 / 15.0 for all
  eight), so only absolute drawdown and exit-reason stability discriminate (report §4). The default grid
  and the `ultra_late_tight` probe were Product Owner decisions (#144) — **the first one has been taken**,
  see the next bullet; engine work is #145, live-path parity is #147, sandbox #151, acceptance #152.
  Mirrored in `project-context.md` §18 and roadmap block W (§8).
- **Product Owner decision, 2026-09-08: `ultra_late_tight` is the production default grid.**
  `config.trailing_stop.steps` defaults to `2.0→1.9`, `2.5→2.4`, `3.0→2.9` in `trading_config.TRAILING_STOP`
  (#144), and `config.trailing_stop.enabled` stays `false` — the choice switches nothing on and touches no
  locked configuration (126 / 36 / 102 / 118). Why this grid: 110 434 ₽ against 103 216 ₽ for `ref139`
  (+7 218 ₽), PF 1.60 vs 1.55, 3 162 vs 3 118 trades, the best average walk-forward PF of the lattice (1.62) with a
  +1 366 ₽ worst-window floor against +745 ₽ (`three_step_steady` floors higher, at +1 512 ₽), more equity than the base
  at every cost-stress node (19 364 ₽ vs 16 708 ₽ at the worst node, commission 0.15 % + 20 b.p.; DD degrades
  +64.45 vs +68.60 pp), and one of the smallest behavioural diffs in the lattice (141 exit-reason flips = 4.3 %,
  Spearman ρ 0.9966 — only `two_step_aggressive` is closer, at 126 flips / 3.8 %). Accepted trade-off:
  composite stability score 46.3 vs 47.3 for `ref139` and daily MaxDD 3.06 vs 2.72 pp — a deliberate swap,
  because the score is a summary of this lattice and not an objective (report §4).
- **What the decision does not change.** `ref139` (the #139 grid) remains the parity anchor: #147 injects it
  explicitly, and it stays out of the production defaults; the frozen evidence in
  `analytics/issue-139-trailing-stop-new-level/` and `analytics/issue-143-trailing-robustness/` is untouched,
  and #143 / #155 stay closed with a pointer comment. Residual risk: the step margin is **0.1R**, so slippage or
  a gap on a synthetic market order eats a visible share of the locked profit — #151 owes a defensive price
  step and #152 owes the break-even slippage measured against 0.1R, on which the leave / tune / rollback
  verdict is built. Decision text is recorded in the bodies of #142 (Decision section) and #144 (§1–§2).

### Production-path parity (Issue #147, epic #142 gate)

The package `analytics/issue-147-trailing-production-parity/` runs the production path
(`StrategyEvaluator` → `portfolio_simulator`, bash-launched shard processes without
multiprocessing, per-ticker cache) on locked `test_20260830_new_level` (id=126, SHA
`dfc855195ade…`), period `2024-08-01` … `timestamp < 2026-08-21`, 28 tickers, 50 000 RUB /
slot 10 000 RUB / max 5, and books three runs: A_prod (trailing off), B_prod (the `ref139`
grid injected explicitly), B_default (steps from `trading_config.TRAILING_STOP` =
`ultra_late_tight`). Gate verdict: A_prod=PASS (tight parity with the #139 book A: equity
95179.91 vs 95180.01 RUB, n 2649=2649, PF 1.41, WR 24.2%, daily MaxDD 6.49=6.49 pp, exit
reasons match, candidate-level entries 0/0); B_prod=FAIL on the daily MaxDD band only
(4.01 vs 2.74 pp, Δ1.27 > the 1.0 pp band declared before the run in run.md v3):
structural live-loop feedback — an early trailing exit frees the ticker and the engine takes
new entries (+789/−15 candidates against the overlay's 3305), which the #139/#143 overlay
holds fixed by construction; exit mechanics are bit-for-bit per #145 grid_check (0/3305 on
both grids); B_default=PASS on the directional criteria against the published
`ultra_late_tight` #143 (equity 120753.7 RUB vs 110433.68, PF 1.56 vs 1.60, n 3794 vs 3162,
daily MaxDD 3.96 vs 3.06 pp, split stop>trailing>take). OVERALL=FAIL: the B_prod
criterion verdict is escalated for TL/PO sign-off (draft comment in the package report.md);
tolerances were not fitted post-hoc. Protected rows 126/36/102/118 untouched; nothing written
to `paper_positions` / `backtest_results`.

## 36. Operating the trailing-stop configuration contract (Issue #144)

- `config.trailing_stop` is now a first-class key of `strategies.config` (JSONB, no schema
  migration): `{"enabled": bool, "steps": [{"trigger": 2.0, "stop": 1.9}, ...]}` — both numbers are R
  multiples measured from the entry. **There is no `take_partial` in this contract**, and no
  `trigger_r` / `lock_r` naming: a partial take was never part of the approved `ultra_late_tight` grid,
  so #144 does not invent one.
- The contract lives in `backend/app/analytics/trading_config.py`: `TRAILING_STOP` (defaults + bounds),
  `get_trailing_stop_config()`, `normalize_trailing_stop()`, `validate_trailing_steps()`,
  `resolve_trailing_stop()` and `require_valid_trailing_stop()`. `validate_trailing_steps()` never raises
  and never mutates its input — it returns the sorted, de-duplicated set of **stable reason codes**
  (`trailing_disabled`, `trailing_step_invalid`, `trailing_not_monotonic`, `trailing_too_many_steps`);
  an empty list means "accepted". Renaming a code is a contract break: #149 surfaces these strings
  verbatim, and `require_valid_trailing_stop()` is the only helper that turns them into `ValueError`.
- Bounds are read from `TRAILING_STOP` and are the single source of truth (engine, API and frontend must
  never restate them): `0 < trigger <= 3.5` (`min_trigger` is exclusive, so a step at 0R is not a step),
  `0 <= stop <= 3.0`, `stop < trigger` always, at most `max_steps` = 6 steps. A step must be a **dict
  with finite numeric `trigger` and `stop`**: `bool` is not a number, a list of `[trigger, stop]` pairs
  is *not* accepted, and no other key spelling works as an alias. Stops may not fall as triggers rise
  (`trailing_not_monotonic`), the same trigger twice with two different stops is
  `trailing_not_monotonic`, and an exact duplicate pair is dropped by normalization rather than
  rejected. There is deliberately **no minimum gap** between a trigger and its stop — the production
  ladder lives on 0.1R, so any "gap ≥ 0.5R" heuristic would reject the shipped default.
- `resolve_trailing_stop(config)` is the one call for consumers: `{"enabled", "steps", "reasons"}`. An
  absent key, `None`, `{}` or a non-dict block resolve to `{"enabled": false, "steps": [], "reasons": []}`,
  which is why configs without the key behave exactly as before #144. `enabled` is strict — only a real
  bool or `'1' / 'true' / 'yes' / 'on'` arm the ladder, any other truthy value normalizes to `false`.
  `normalize_trailing_stop()` is idempotent, keeps float precision (1.9 / 2.4 / 2.9 are never rounded to
  0.5R), sorts by `(trigger, stop)` and drops structurally broken steps: it is a canonicalizer, **not**
  the gatekeeper, so always validate the raw list.
- Shipped default `TRAILING_STOP`: `enabled=false`, steps `2.0→1.9 / 2.5→2.4 / 3.0→2.9` — the
  `ultra_late_tight` grid of §35 — with bound defaults `max_steps=6`, `min_trigger=0.0` (exclusive),
  `max_trigger=3.5`, `min_stop=0.0`, `max_stop=3.0`. Editing the ladder switches nothing on by itself.
- **Applied since #145 — still no write-path gate.** `validate_config()` **does not exist in this
  repository**, so nothing refuses a malformed ladder on strategy create/update or on a Lab run: an
  `enabled=true` block with no usable steps still saves and simply reports `trailing_disabled`.
  `require_valid_trailing_stop()` remains the gate #149 is expected to call — #146 shipped the Lab
  editor without it (§38): the editor validates client-side with these same codes, but POSTing a
  malformed ladder still succeeds. What changed in
  #145 is the read path: `app.analytics.trailing_stop` resolves the block and
  `StrategyEvaluator.on_bar`, the `levels_reversal` plugin, `portfolio_backtest` /
  `portfolio_simulator` and walk-forward arm the ladder from it, and `EXIT_TRAILING` is now emitted by
  the production engine (see §37). The engine fails safe — a ladder the validator refuses is never
  armed — so backtest, Lab-run and portfolio results may be described as trailing-stop-enabled only
  when the config carries a valid block with `enabled=true`; paper (`paper_trader`) and sandbox
  (`live_executor`) still ignore the block until #148 / #151.
- Legacy: the pattern-level `trailing_stop` / `trailing_step` parameters in `pattern_registry.py` are a
  different contract that #144 does not touch — do not confuse them with `config.trailing_stop`.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_contract.py tests/test_trading_config.py`
  (39 together: 33 contract + 6 config). The full issue acceptance set — those two plus
  `test_levels_sr_support.py`, `test_resistance_zone_veto.py`, `test_strategy_plugin.py`,
  `test_pattern_registry.py`, `test_issue139_analysis.py` and `test_issue155_analysis.py` — stands at
  **99 passed** as of 2026-09-08; `--collect-only` still guards the Lab API import.

## 37. Operating the production trailing stop (Issue #145)

- One ladder, `backend/app/analytics/trailing_stop.py`, is the only exit-ratchet in the repo:
  `StrategyEvaluator.on_bar` (single-ticker backtest), `LevelsReversalStrategy.check_exit` /
  `manage_position` (the plugin mirror), `portfolio_backtest` → `portfolio_simulator` and
  `run_walkforward` all call `evaluate_bar(...)`. Do not re-implement the ratchet anywhere else: the
  plugin is a mirror of the brain, and a second copy is exactly how #41 parity broke before.
- Fill convention inside a managed bar: stop check → take check → arm. A rung armed by bar *i*
  applies from bar *i+1*; the entry bar never arms anything (matching #139, whose `path` excludes
  the entry bar). Stop beats take when both are reachable in one bar. Reasons: `stop` (ladder never
  moved the stop — the baseline case), `trailing` (raised stop hit), `take`.
- Turning it on is a config decision, not a code change: add
  `{"trailing_stop": {"enabled": true, "steps": [...]}}` to a strategy's `strategies.config`
  (R multiples from the entry; the shipped default ladder is `ultra_late_tight`, §35). Locked
  configs 126 / 36 / 102 / 118 are untouched — none of them carry the block, and #145 is no
  reason to edit them.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_contract.py
  tests/test_trailing_stop.py`.

## 39. Trailing stop API integration (Issue #149)

- **Write gate**: `POST /api/strategies` now calls `require_valid_trailing_stop()` before
  saving. A bad ladder returns `422` with body `{"detail": {"message": ...,
  "reason_codes": [...]}}` — stable string codes from `TRAILING_REASON_CODES`
  (`trailing_step_invalid`, `trailing_not_monotonic`, `trailing_too_many_steps`).
  The client-side check from #146 is no longer the only line of defence.
- **List metadata**: `GET /api/strategies` attaches `trailing_stop` to every item:
  `{"enabled": bool, "steps": [...], "reasons": [...]}` — the output of
  `resolve_trailing_stop()`. The UI sees validity without re-running the validator.
- **Backtest results**: `_run_job` in `strategy_jobs.py` stores `exit_reasons` in
  `backtest_results.metrics.exit_reasons` — a breakdown of closes by reason
  (`stop`/`take`/`trailing`/etc.).
- **Paper API**:
  - `GET /api/paper-trading/overview` — four new fields in `summary`: `trailing_closed`,
    `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count`.
  - `GET /api/paper-trading/positions` — SELECT includes `trailing_enabled`,
    `current_stop_price`, `step_reached`, `risk_r`.
  - `status=closed` filter already included `closed_trailing` (from #148); no regression.
- **Live API**:
  - `GET /api/live-trading/positions` — SELECT includes the same trailing fields.
  - `_build_where` extended: `status=closed` now includes `closed_trailing`.
  - `dynamics` counts wins as `closed_take OR (closed_trailing AND pnl_rub > 0)` —
    matching the paper convention.
- **Migration**: `20260915_002_live_trailing.py` — same columns as #148, but for
  `trading.live_positions`. Idempotent (`ADD COLUMN IF NOT EXISTS` + backfill).
- **Tests**: `cd backend && python -m pytest -q tests/test_trailing_api.py
  tests/test_trailing_contract.py tests/test_trailing_schema_endpoint.py
  tests/test_trailing_stop.py` — green.
- **Caveat (resolved)**: the 4 red tests in `test_paper_trailing_stop.py` that initially
  looked like #149 regressions were a **stale Docker image** artefact — the container had been
  built before `#148-fix` (`c93f39d`) landed, so `monitor_open()` inside the image still lacked
  the trailing-wiring fix. After `docker compose build backend && docker compose up -d backend`
  the host code is current and the whole paper-trailing suite (`test_trailing_api.py`,
  `test_trailing_contract.py`, `test_trailing_schema_endpoint.py`, `test_trailing_stop.py`,
  `test_paper_trailing_stop.py`) is green. 126 / 36 / 102 / 118 stay untouched — the block is
  not present there, and #145 must not be used as a reason to edit them. Lab editing is #146,
  API validation is #149.
- Reading a run: backtest trades carry `step_reached` only when a ladder was armed (no key = the
  pre-#145 shape); `portfolio_simulator.metrics` now carries `exit_reason_counts`,
  `trailing_exits`, `take_exits`, `initial_stop_exits`, `trailing_exit_share_pct`. A ladder that
  fires earlier also releases a slot earlier, so `n_trades` rises and `skipped_entries_no_slot`
  falls — that is the mechanism behind #139's 2 649 → 3 118 and #143's 3 162, not a bug.
- Reproduce the acceptance evidence (this is the `critical`-task regression, SOP red line #3):
  ```
  # after (this branch)
  python reports/Arctic/145_trailing-evaluator/regression_run.py --label after --out after.json
  # baseline (main, e.g. a scratch worktree: git worktree add <tmp>/wt origin/main)
  python reports/Arctic/145_trailing-evaluator/regression_run.py --label baseline \
      --backend <tmp>/wt/backend --out baseline.json
  python reports/Arctic/145_trailing-evaluator/regression_run.py \
      --compare baseline.json after.json --verdict regression_verdict.json   # regression_match: true
  # the ladder against the published #143 book (no DB, path caches only)
  python reports/Arctic/145_trailing-evaluator/grid_check.py
  ```
  Both scripts are **read-only** (`SELECT` on `trading.strategies` / `trading.candles_1min_raw`);
  nothing in #145 writes trades, results or strategy rows. From the host, point the warehouse at
  `--db-host 127.0.0.1` (the `.env` default `postgres` resolves only inside the compose network).
- Parity with the analytics books is deliberately *not* claimed here: #147 owns that gate. What
  #145 shows is `grid_check.json` — the production ladder reproduces #143's per-trade exits on both
  `ref139` and `ultra_late_tight` with 0 real mismatches over 3 305 trades, and replays the book to
  the published figures once #143's 4-decimal rounding convention is re-applied.
- Gotchas: `Position` (plugin dataclass) grew `initial_stop` / `step_reached` / `trailing` fields —
  always pass them by keyword; the evaluator keeps its ladder in `position['trailing_state']`, and
  `position['stop']` is the *next-bar* stop while `trailing_state.live_stop` is what the current bar
  was checked against. Metrics going into `backtest_results` still pass through `_json_safe` (#116).
## 38. Operating the Lab trailing-stop editor (Issue #146)

- The Lab now edits `config.trailing_stop`: a toggle plus a `trigger → stop` rung table, in the
  config rail between «Risk / Reward» and «Тест». It is a **top-level exit block, not a pattern
  parameter** — do not move it into `PATTERN_REGISTRY['levels_reversal']`; that would break both
  the schema-driver and the #144 contract.
- Schema-driven, honestly: the section renders only after `GET /api/strategies/trailing-schema`
  answers, and reads defaults, `max_steps`, all four bounds, the `0.1R` input resolution, the
  approved grid's **name and values** and the reason-code vocabulary from that payload.
  `trading_config.get_trailing_stop_schema()` is the producer; `TRAILING_STOP` stays the single
  source of truth and is **not** mutated by it (the two new keys, `default_grid` and `input_step`,
  are labels added on the way out). There is no fallback ladder in the frontend: if the endpoint
  is unreachable the section is absent, so a stale bundle can never offer a ladder nobody approved.
  - #146 req. 1 said to *request* this endpoint rather than build it, and #149 owns its
    long-term shape («endpoint схемы … для schema-driven UI #146»). It landed here so #146 is
    demoable end-to-end; **#149 inherits it — extend `get_trailing_stop_schema()`, do not fork a
    second schema object into the router.**
- Files: `frontend/src/trailingStop.ts` (all logic — parse, validate, payload; pure, no React),
  `frontend/src/components/TrailingStopFields.tsx` (renderer, zero trailing numbers),
  `frontend/src/exitReasons.ts` (exit-reason labels/tones), wired in `StrategyLab.tsx`.
  The issue named `pages/strategies/StrategyConfigPanel.tsx`, `lib/api.ts`, a `strategiesApi`
  object, `components/ui/{Button,Input,Select}` and `lucide-react` — **none of those exist in this
  repo**; the equivalents are `components/StrategyLab.tsx`, `src/api.ts` free functions, the
  Lab-local `Section`/`numInput` primitives and inline SVG (`PatternIcon` precedent). Follow the
  repo, not the issue's paths, when touching this.
- `validateLadder()` mirrors `validate_trailing_steps()` rung for rung — exclusive `min_trigger`,
  inclusive other bounds, `stop < trigger`, sort-then-check, exact duplicates collapsed silently,
  two stops on one trigger and falling stops both `trailing_not_monotonic`, bounds enforced even
  when the toggle is **off**. `src/trailingStop.test.ts` pins the two languages to the same
  published `grids.json`. It is a pre-flight, not a gate: see the caveat below.
- Untouched configs stay untouched (the requirement most likely to regress). `buildTrailingStopPayload()`
  returns `null` unless the operator touched the block, and the `config` memo **spreads** it
  conditionally, so a strategy that never carried `trailing_stop` saves without the key. Loading a
  stored strategy sets `touched=false`; any edit flips it once, permanently. Note the deliberate
  asymmetry: touching the block and saving with the toggle off *does* write an explicit
  `enabled:false` — an opt-out is an opinion, absence is not.
- Production default renders correctly: `ultra_late_tight` is three rungs on a 0.1R gap
  (`2.0→1.9, 2.5→2.4, 3.0→2.9`), so `step={input_step}` must stay at 0.1 and the cells must be
  **controlled string state** — an uncontrolled input or a `step="0.5"` snaps 1.9 to 2.0 in the
  browser and the frontend then ships a ladder #144 rejects. Round-trip covered by tests.
- Exit reasons: the trade table used to render a binary take/stop, which silently labelled every
  `trailing` exit «стоп» — the ladder's own output was indistinguishable from being stopped out.
  `EXIT_REASON_ORDER` now covers the closed set (`stop, take, trailing, holding, signal, session`)
  with `trailing` in sky, distinct from the red of `stop`. When #147 or the engine adds a reason,
  extend that array; unknown values fall through to a neutral chip rather than disappearing.
- **Caveat / known gap: #146 does not gate the write path.** Saving a malformed ladder still
  succeeds over the API, because the POST handler does not call `require_valid_trailing_stop()` —
  that gate is #149's deliverable («валидация общей функцией #144; отказ = 422»). Until it lands,
  the client-side check can be bypassed by any non-Lab client. The #145 engine remains the safety
  net: it refuses to arm a ladder the validator rejects, so a bad config changes no exits.
- **`config_hash` does not exist in this repository** (zero hits outside issue text). Requirement
  4's «hash must change when trailing is enabled» is therefore unverifiable here; it is satisfied
  only in the trivial sense that the saved `config` JSONB genuinely carries the block. If a hash is
  wanted, it needs its own issue.
- Tests: `cd backend && python -m pytest -q tests/test_trailing_schema_endpoint.py
  tests/test_trailing_contract.py tests/test_trailing_stop.py` and
  `cd frontend && npx tsc --noEmit && npx vitest run`.
- Not done here, and not accidentally shippable: no screenshots (EN/RU) and no
  `docker compose up -d --build frontend` — this was verified against a live uvicorn instance and
  a production `vite build`, not a browser. #150 (Paper/Live panels) still has to surface trailing
  state to the operator.





## 40. Trailing metrics in Paper / Live panels (Issue #150)

### What was done
- **Backend**: `_build_where` in `paper_trading_jobs.py` and `live_trading_jobs.py` now accepts `exit_reason` (query filter on `exit_reason` in positions). Endpoints `GET /api/paper-trading/positions` and `GET /api/live-trading/positions` accept `?exit_reason=...`.
- **Backend**: `PaperOverview.summary` already includes `trailing_closed`, `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count` (added in #149).
- **Frontend i18n**: new module `frontend/src/i18n/config.ts` — single source of `AppLocale` (`"ru" | "en"`) and `resolveAppLocale()`. `LabLocale` in `patternLab.ts` and `trailingStop.ts` is now an alias of `AppLocale`.
- **Frontend trailingStatus.ts**: new pure module (`frontend/src/trailingStatus.ts`) — labels, tone classes, formatting, and summary cards for trailing metrics. No trailing label or number hardcoded in TSX.
- **PaperTradingPanel.tsx**: added columns `exit_reason` (with funnel filter), `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`. Added trailing summary cards block above factor filters.
- **LiveTradingPanel.tsx**: added columns `exit_reason` (with funnel filter), `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r` in both tables (open + history). Trailing cards computed from data (no dedicated summary endpoint for Live).
- **types.ts**: `PaperSummary` extended with trailing fields; `PaperPosition` and `LivePosition` gained `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`.
- **api.ts**: `getPaperPositions` and `getLivePositions` accept `exit_reason`.

### Tests
- `backend/tests/test_issue150_exit_reason_filter.py` — 6 tests for `_build_where` (paper + live).
- `frontend/src/trailingStatus.test.ts` — 25 tests for pure functions.
- All existing tests (91 vitest + 126+ pytest) remain green.

### Deviations from issue description
- Issue #150 description references non-existent file paths and enum values (`eod`, `error`); actual code uses `EXIT_REASON_ORDER` from `exitReasons.ts` and `_build_where` from existing modules.
- Acceptance criterion `docker compose up frontend` is inapplicable — frontend is not Dockerized (only `npm run build`).
- Full i18n migration not performed — only new labels use the i18n layer; existing Russian strings in TSX left as-is.

### Verification commands
```bash
cd backend && python -m pytest tests/test_issue150_exit_reason_filter.py -v
cd frontend && npx vitest run src/trailingStatus.test.ts
cd frontend && npx tsc --noEmit
```
## 41. Operating live trailing stop (Issue #151)

Completed 2026-09-16. Live/sandbox trailing-stop ratchet, restart, and kill-switch.

### Architecture overview

- **Arming**: `process_signal()` arms trailing on position open if `strategy_config.trailing_stop.enabled=true` and kill switch is OFF. Zero broker calls. Parameters written to `trading.live_positions`: `trailing_enabled=true`, `trailing_steps` (JSONB), `risk_r=entry-stop`, `current_stop_price=stop_price`, `step_reached=0`.
- **Ratchet**: `monitor_positions()` calls `_apply_trailing(row, current_price)` for each active position with `trailing_enabled=true`. Rebuilds `TrailingState` from DB, evaluates current price as single bar, conditional UPDATE `WHERE id=%s AND step_reached < %s` ensures monotonicity.
- **Exit**: When `current_price <= current_stop_price`, cancels take, submits sell-limit at current price (blocking=False, deferred if rate limit exhausted). Records `exit_price_model`, `exit_price_actual`, `slippage_bp`, `slippage_r`, `lots_executed`. Status `closed_trailing`, reason `trailing`.
- **Broker amend (Issue #175)**: each ratchet also moves the broker `STOP_LOSS` by duplication — `post_stop_order()` for the new stop, `get_stop_orders()` to confirm it, then `cancel_stop_order()` for the older, lower one. The DB ratchet is kept even when the amend fails (a stop never moves down) and the next cycle retries; failures log `trailing_amend_failed`, an inconclusive confirmation logs `trailing_amend_unverified` and queues the old stop for `_cancel_pending_stops()`.
- **Kill switch**: `_refresh_kill_switch()` reads `trading.app_settings.trailing_kill_switch` before each monitoring cycle. ON pauses arming/ratchet; armed positions keep state. Fail-safe: DB error → kill switch ON.
- **Advisory lock**: `pg_try_advisory_lock(151001)` in `initialize()`, released in `shutdown()`. Prevents multiple executor instances.
- **Restart safety**: State restored from DB columns; no in-memory state. Idempotent replay.

### Database

- Migration `20260916_001_live_trailing_runtime.py`: extends `live_positions.status` CHECK to include `closed_trailing`, `closed_broker`; adds `exit_price_model`, `exit_price_actual`, `slippage_bp`, `slippage_r`, `lots_executed`; creates `trading.app_settings` table.
- Columns `trailing_enabled`, `trailing_steps`, `risk_r`, `current_stop_price`, `step_reached` already exist (migration `20260915_002`).

### Configuration

- `LIVE_TRADING.trailing_kill_switch`: default `false`, runtime override via `trading.app_settings`.
- `LIVE_TRADING.live_trailing_enabled`: default `true`, global toggle.
- `LIVE_TRADING.trailing_protective_ticks`: default `5`, protective offset (in `min_price_increment` ticks) between the broker stop trigger and the limit price it activates (Issue #175).
- `LIVE_TRADING.trailing_ticker_allowlist`: default `[]`, optional ticker filter.
- `LIVE_TRADING.broker_stop_enabled`: default `true`; `false` disables broker `STOP_LOSS` arming and leaves only the synthetic fallback (Issue #175, debug switch).
- `LIVE_TRADING.protection_retry_seconds`: default `30`, base of the exponential backoff after a failed `PostStopOrder`.
- `LIVE_TRADING.broker_stop_verify_interval_seconds`: default `60`, period of the `GetStopOrders` invariant pass.
- `LIVE_TRADING.oco_check_delay_seconds`: default `60`, OCO grace period (also the deadline for a deferred amend cancel).
- `LIVE_TRADING.oco_check_attempts`: default `3`, OCO verification attempts before the final critical alert.
- `LIVE_TRADING.operations_lookback_hours`: default `24`, `GetOperations` window used for fill reconciliation.
- `LIVE_TRADING.entry_token_reserve`: default `1.0`, tokens an entry call must leave in the bucket for protection calls.
- `LIVE_TRADING.orphan_stop_sweep_enabled`: default `true`, master switch of the account-wide orphan stop sweep (Issue #199).
- `LIVE_TRADING.orphan_stop_sweep_interval_seconds`: default `300`, minimum period between two sweeps; a non-positive value means "every monitoring cycle".
- `LIVE_TRADING.orphan_stop_confirmations`: default `2`, how many consecutive sweeps must report the same orphan before it may be cancelled; below 1 is clamped to 1 and rejected at startup.
- `LIVE_TRADING.orphan_stop_grace_seconds`: default `900`, protection window for a stop this process armed recently.
- `LIVE_TRADING.orphan_stop_max_cancels`: default `3`, per-pass ceiling; `0` is watch-only, exceeding the cap makes the whole pass fail closed.
- `LIVE_TRADING.orphan_stop_alert_interval_seconds`: default `3600`, throttle of the fail-closed / cancelled Telegram alerts.


### Operations

**Enable trailing**:
1. Ensure strategy has `trailing_stop.enabled=true` in config.
2. Verify `trading.app_settings.trailing_kill_switch = false`:
   ```sql
   SELECT value FROM trading.app_settings WHERE key='trailing_kill_switch';
   ```
3. Start executor: `python -m app.analytics.run_live_trading --strategy <name> --duration-minutes 30`

**Pause trailing (kill switch)**:
```sql
UPDATE trading.app_settings SET value='true'::jsonb, updated_at=now() WHERE key='trailing_kill_switch';
```
No restart required. Armed positions keep state; new positions not armed.

**Resume trailing**:
```sql
UPDATE trading.app_settings SET value='false'::jsonb, updated_at=now() WHERE key='trailing_kill_switch';
```

**Monitor trailing exits**:
```bash
grep 'trailing_exit' reports/live-executor/live-executor-*.log
```
Structured log: `ticker, entry, initial_stop, final_stop, step_reached, risk_r, model_price, actual_price, slippage_bp, slippage_r, lots_requested, lots_executed, position_id`.

**Check advisory lock**:
```sql
SELECT pg_try_advisory_lock(151001);  -- false if another instance running
```

### Testing

68 tests in `test_live_executor.py` (43 new for #151):
- Arming: `test_open_position_arms_trailing_when_enabled`, `test_open_position_skips_trailing_when_disabled_in_config`, `test_open_position_skips_trailing_when_kill_switch_on`, `test_open_position_no_trailing_without_strategy_config`
- Ratchet: `test_apply_trailing_returns_none_when_disabled`, `test_apply_trailing_returns_none_when_kill_switch_on`, `test_apply_trailing_returns_none_when_no_steps`, `test_apply_trailing_returns_none_when_price_below_next_step`, `test_apply_trailing_ratchets_when_price_reaches_trigger`, `test_apply_trailing_returns_none_when_step_already_reached`
- Execution facts: `test_close_position_records_model_and_actual_price`, `test_close_position_handles_missing_actual_price`, `test_close_position_trailing_uses_closed_trailing_status`
- Rate limiter: `test_token_bucket_try_acquire_returns_true_when_token_available`, `test_token_bucket_try_acquire_returns_false_when_exhausted`, `test_token_bucket_try_acquire_recovers_over_time`, `test_broker_call_nonblocking_returns_none_when_exhausted`, `test_broker_call_blocking_still_works`
- Config validation: 9 tests for `trailing_kill_switch`, `live_trailing_enabled`, `trailing_protective_ticks`, `trailing_ticker_allowlist`
- Kill switch integration: `test_kill_switch_prevents_trailing_arming`, `test_kill_switch_preserves_armed_positions`, `test_invalid_trailing_config_disables_arming`

### Verification commands

```powershell
# Тесты
cd f:\GIT\trading-terminal\backend; python -m pytest tests/test_live_executor.py -v

# Локальный запуск миграций (PowerShell, без Docker):
cd f:\GIT\trading-terminal\backend; $env:APP_DATABASE_URL=""; $env:POSTGRES_HOST="localhost"; python -m alembic upgrade head

# Docker запуск:
docker compose exec backend python -m alembic upgrade head
```

### Known limitations

- `EXIT_TAKE` from replay is ignored (take is resting order, existing reconciliation handles it).
- Partial fills (`lots_executed < size_lots`) logged but not retried.
- `broker_position_vanished` event type not yet implemented (currently logs warning).

## 42. live_positions schema contract and fail-fast preflight (Issue #173)

### Why

The runtime DDL in `ensure_live_positions_table()` created 20 columns and a five-status CHECK, while Alembic (`20260915_002`, `20260916_001`) requires 30 columns and seven statuses. A production database built by the executor without migrations would fail on the trailing columns (`UndefinedColumn`) and could not store `closed_trailing` / `closed_broker`. Preflight did not inspect the schema at all, so drift surfaced during trading instead of at startup.

### What changed

| File | Change |
| --- | --- |
| `backend/app/analytics/live_schema.py` | **New module**: contract constants (30 columns, 7 statuses, `app_settings` keys), idempotent DDL `ensure_live_positions_schema()` (`LIVE_SCHEMA_STATEMENTS`), `inspect_live_schema()`, `validate_live_schema()`, `assert_live_schema()` / `LiveSchemaError`, `describe_live_schema_problems()`, `live_schema_summary()` |
| `backend/app/analytics/live_executor.py` | `ensure_live_positions_table()` is now a thin wrapper over the contract module; `initialize()` calls `assert_live_schema()` immediately after the DDL |
| `backend/app/analytics/live_executor_preflight.py` | New blocking check `live_positions_schema` plus a `details.live_schema` summary |
| `backend/tests/test_live_schema.py` | **New**: 23 tests (contract, DDL idempotency and order, drift detection, warnings, executor fail-fast) |
| `backend/tests/test_live_executor.py` | `FakeDB` now answers `information_schema.tables` / `information_schema.columns` / `pg_constraint`; defaults describe a fully migrated schema, constructor arguments allow simulating drift |

### Behaviour

- The DDL is idempotent and a superset of both migrations: `CREATE TABLE IF NOT EXISTS` (30 columns, seven-status CHECK), the active index, `ADD COLUMN IF NOT EXISTS` for the trailing and execution-fact columns, the `current_stop_price` backfill, `DROP/ADD CONSTRAINT` for the status CHECK, then `app_settings` creation and seeding.
- Blocking errors: missing `trading.live_positions` or `trading.app_settings`, missing columns, missing or narrow status CHECK, catalog read failures.
- Warnings only: missing `app_settings` keys (the executor already falls back to safe defaults) and columns outside the contract.
- The first three DDL statements keep their historical positions because `test_runtime_migration_is_idempotent` asserts on them.

### Verification

```powershell
# 23 new + 68 existing executor tests
cd f:\GIT\trading-terminal\backend; python -m pytest tests/test_live_schema.py tests/test_live_executor.py -q

# Read-only contract check against the real database
cd f:\GIT\trading-terminal\backend; python ..\reports\173-issue-173-live-schema-preflight\verify_schema_contract.py
```

Production database result (2026-09-18): `ok: true`, `columns_found: 30/30`, all seven statuses allowed, both `app_settings` keys present, no warnings.

### Known limitations

- `live_executor_preflight.py` still requires Linux `/proc` and a healthy backend on `localhost:8000`; only its schema part is unit-testable on Windows.
- Two pre-existing stale assertions still expect the pre-#149 two-status `closed` group: `test_live_trading_api.py::test_live_filters_target_closed_positions_ticker_and_dates` and `test_paper_trading_monitoring_api.py::test_monitoring_filters_support_closed_group_ticker_and_dates`. Tracked in Issue #179, out of scope for #173.





## 43. LiveExecutor resilience (Issue #174)

Completed 2026-09-19. Cycle resilience, safe shutdown, advisory lock on dedicated connection, heartbeat and metrics.

### Why

The LiveExecutor could die on a single transient error (DB timeout, broker gRPC failure), leaving positions unprotected. The advisory lock `pg_try_advisory_lock(151001)` was acquired on a pooled connection and could leak if `shutdown()` used a different pooled connection. On shutdown with `close_positions_on_shutdown=false`, stop/take orders were cancelled, leaving positions unprotected between executor restarts.

### What changed

| File | Change |
| --- | --- |
| `backend/app/db/db_manager.py` | New methods: `get_dedicated_connection()`, `release_dedicated_connection(conn)`, context manager `dedicated_connection()`. Existing consumers (`select`, `execute`, `insert`) unchanged. |
| `backend/app/analytics/live_executor.py` | Advisory lock on dedicated connection (`_lock_conn`); loop protection with `MAX_CONSECUTIVE_ERRORS = 5`; per-unit isolation in `process_latest_bars()`, `refresh_contexts()`, `monitor_positions()`; safe shutdown leaves open positions protected; heartbeat metrics (`heartbeat_ts`, `iterations_total`, `errors_total`, `errors_consecutive`, `last_error_at`); new method `get_metrics()` for external monitoring (task E #177). |
| `backend/tests/test_live_executor.py` | 10 new tests: advisory lock, loop protection, isolation, safe shutdown, heartbeat. Total: 78 tests. |

### Behaviour

- **Loop protection**: `monitor_positions()` and `process_latest_bars()` are wrapped in `try/except` inside `run()`. Each failure increments `_consecutive_errors`; success resets it to 0. At `MAX_CONSECUTIVE_ERRORS = 5` consecutive failures, a critical alert is logged and the executor stops gracefully.
- **Per-unit isolation**: errors in one ticker (in `process_latest_bars()` / `refresh_contexts()`) or one position (in `monitor_positions()`) are logged with `exc_info=True` and do not break processing of other units.
- **Safe shutdown**: when `close_positions_on_shutdown=false` (the default), `shutdown()` does **not** cancel stop/take orders for open positions and does **not** clear `broker_stop_id` / `broker_take_id` in the DB. Only pending entry orders are cancelled. Open positions survive SIGTERM/SIGINT with broker-side protection intact. When `close_positions_on_shutdown=true`, behaviour is unchanged (positions are flattened).
- **Advisory lock**: acquired on a dedicated connection (`get_dedicated_connection()`) in `initialize()`, released on the **same** connection in `shutdown()`. The connection is held outside the pool auto-release for the entire executor lifetime.
- **Heartbeat**: `heartbeat_ts` and `iterations_total` are updated after each successful loop iteration. `errors_total` and `last_error_at` are updated on each error. `get_metrics()` returns `{"heartbeat_ts", "iterations_total", "errors_total", "errors_consecutive", "last_error_at"}` (plus the risk fields since #176 and the alerting counters and heartbeat windows since #177; the snapshot is persisted and served by `GET /api/live-trading/metrics`, see §45).

### Commands

```bash
# Unit tests (78 tests)
cd backend && python -m pytest tests/test_live_executor.py -q

# Check advisory lock is held
psql -c "SELECT pg_try_advisory_lock(151001);"  # returns false if executor is running
```

### Known limitations

- ~~`MAX_CONSECUTIVE_ERRORS = 5` is a module constant~~ **closed by #177**: the threshold is now the `LIVE_ALERTING.max_consecutive_errors` key (env `LIVE_MAX_CONSECUTIVE_ERRORS`, range `[1, 100]`); the module constant survives only as the fallback default. See §45.
- ~~Heartbeat is in-memory only~~ **closed by #177**: the `get_metrics()` snapshot is persisted to `trading.app_settings['live_executor_metrics']` (JSONB) and served by `GET /api/live-trading/metrics`, which computes the heartbeat age and the `stale` flag itself. See §45.

### SSL certificates for T-Bank gRPC

The T-Bank Invest API requires a root certificate for gRPC connections. On a Windows host, Python/gRPC does not find system certificates automatically, so an environment variable is used:

```bash
export GRPC_DEFAULT_SSL_ROOTS_FILE_PATH="$(pwd)/backend/certs/tbank-root.pem"
```

This path is set automatically in `start_processes.sh`. If the certificate is missing or invalid, `TinkoffSandboxClient.check_balance()` will fail with `CERTIFICATE_VERIFY_FAILED`.

**Diagnostics:**
```bash
cd backend
python -c "from app.broker.tinkoff_sandbox import TinkoffSandboxClient; print(TinkoffSandboxClient().check_balance())"
```

If a balance (number) is returned, the certificate works. If `CERTIFICATE_VERIFY_FAILED`, verify that `backend/certs/tbank-root.pem` exists and contains a valid PEM certificate.


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

**The policy lives in one place.** `trading_config.CANARY` (`enabled=False`, `ticker='SBER'`, `max_lots=1`, `max_open_positions=1`), the ranges `CANARY_BOUNDS` (`max_lots` and `max_open_positions` inside [1, 100]) and the env map `CANARY_ENV` - three knobs only (`CANARY_ENABLED`, `CANARY_TICKER`, `CANARY_MAX_LOTS`), because `max_open_positions` is config-only: a canary holding two positions is not a canary. `get_canary_config()` returns an isolated copy and merges defaults → env → a caller dict; a **blank** knob counts as unset and keeps the default, a **malformed** one raises `ValueError` at startup (`_env_strict_bool` for the flag, `normalize_canary_ticker` for the name, `_env_bounded_int` for the cap), so the contour never starts on a guess. `validate_canary_values()` applies the same rules to a caller-supplied dict (tests and drill): `canary={'enabled': True, 'max_lots': 999}` is refused, a partially filled dict is not.

**What the executor does differently** - all of it behind `canary_enabled`, an ordinary run stays byte-identical to #199:

* construction logs `CANARY MODE ON: ticker=... max_lots=... max_open_positions=... confirmations=2` and clamps `max_open_positions = min(config, canary)`, so an env `MAX_OPEN_POSITIONS=5` can never widen a canary and a canary can never widen the ordinary contour;
* `initialize()` narrows the universe through `_apply_canary_universe()`: the canary ticker, and only when `trading_universe` already marks it `live_trading_enabled`. A typo in `CANARY_TICKER` therefore leaves the universe EMPTY (`Canary universe is EMPTY: ticker=... is not live-enabled` at ERROR) and the loop places no orders at all - fail-closed, not "trade the default";
* `process_signal()` repeats that gate right after the kill switch and before the session window, the order book, sizing and any broker call: another name returns reason `canary_universe`, so a direct call (the drill, a manual replay) cannot smuggle a second ticker in;
* the cap is applied **after** the sizer and after the #176 notional gate and only ever shrinks: `Live canary cap: ticker=SBER size_lots=10 -> 1 reason=canary_cap sizer_reason=risk`. The published sizing reason becomes `canary_cap`, so the run report separates "risk allowed 10 lots, the canary cut it to 1" from "risk allowed 1";
* **pause 1** (`_canary_confirm_entry()`, before `execute_order`): `[CANARY <contour>] Готов к покупке N лот(ов) TICKER по ~X руб., стоп=..., тейк=..., дисбаланс стакана=... Подтвердите (y/n, retry - перечитать стакан)`. `retry` re-reads the order-book aggregate and asks again, at most `CANARY_CONFIRM_MAX_RETRIES=3` times, then the entry is dropped. Anything but an explicit yes (`y` / `yes` / `д` / `да`) is a refusal: `canary_rejections_total` grows, the signal is skipped with reason `canary_not_confirmed`, **zero** mutating broker calls happen, and the critical alert `Canary-вход отклонён оператором` is sent. A `confirm_fn` that raises and EOF (closed stdin, a pipe, `docker compose exec` without `-T`, a detached start) both answer "no" - an unread confirmation is never an approval;
* **pause 2** (`_canary_post_entry_gate()`, after the fill and its protection): `y` keeps monitoring the position; `retry` re-posts ONLY the missing leg through `_canary_rearm_protection()` - an already armed broker stop is never duplicated, because a second active SELL stop would over-sell the position on the next dip and become the next #199 orphan; `n`, or the retry limit, calls `_canary_abort()`.

**An abort is not a flatten.** `_canary_abort()` logs `CANARY ABORT: position_id=... ticker=... lots=... status=... stop_armed=... take_placed=...` at CRITICAL, counts `canary_aborts_total`, sends the critical alert `Canary остановлен оператором — позиция НЕ закрыта` (its last line points at `handover.md §46.11 - ручное закрытие`), sets `shutdown_requested` through `request_shutdown()` and returns `executed=True, reason=canary_aborted` - the order really did happen, the report must not read "opened". It also sets `_canary_abort_no_flatten`, which forces `close_positions_on_shutdown` OFF inside `shutdown()` (`Canary abort: close_positions_on_shutdown forced OFF - the open position keeps its broker protection and waits for the runbook`) even on a deployment that flattens: the money is already in the market with broker protection armed, and liquidating it because a prompt was refused would be the one irreversible action the operator did not ask for. The position is closed by its own stop/take or by runbook C of §46.11.

**Operator view.** `get_metrics()` publishes the identity fields `canary_enabled` / `canary_ticker` / `canary_max_lots` - `None`, never a fake zero identity, when the canary is off - plus the five counters `canary_capped_total`, `canary_rejections_total`, `canary_confirmations_total`, `canary_confirm_retries_total`, `canary_aborts_total`. `GET /api/live-trading/metrics` folds them into their own `canary` section (`enabled`, `ticker`, `max_lots`, `capped_total`, `rejections_total`, `confirmations_total`, `confirm_retries_total`, `aborts_total`) instead of hiding them in `risk`: an operator must see "this loop may only ever buy 1 lot of SBER" at a glance. Read the counters with their semantics - `confirmations_total` counts prompts a human **answered** (a refusal is an answer too), `rejections_total` counts refused entries, `capped_total` counts sizes the cap cut - so one refused request for 10 lots moves all three at once. The `live_start` / `live_entry` alerts gain the `_canary_lines()` block (`Canary: включён`, `Canary-тикер`, `Canary-лимит`, `Подтверждения: 2 паузы: перед ордером и после защиты`, `Сайзер`, `Стоп у брокера`, `Тейк у брокера`) only inside the canary; an ordinary run appends nothing and keeps the #177 body.

**Running a canary.** The confirmations are read from stdin, so the run is a foreground run with the operator at the terminal, inside the entry window (10:00-19:00 MSK):

```bash
# 1. preflight must agree with the contour you intend to trade
docker compose exec -T backend python -m app.analytics.live_executor_preflight
#    on the real contour: PREFLIGHT_EXPECT_CONTOUR=real (see §46.3 step 3)

# 2. foreground start; DURATION_MINUTES is the first argv of `python -m`
docker compose exec -T -e CANARY_ENABLED=true -e CANARY_TICKER=SBER \
  -e CANARY_MAX_LOTS=1 backend python -u -m app.analytics.live_executor 120
```

The first log line to verify is `CANARY MODE ON: ticker=SBER max_lots=1 max_open_positions=1 confirmations=2`, then `Canary universe narrowed: ... -> SBER (max_lots=1)`. Without them the process is an ordinary loop and trades the whole universe.

**Drill (Issue #194).** `194-canary-drill.py` lives with its artifacts in `reports/190-production-trading-infrastructure/194-issue-194-canary-1-lot-sber/` (not part of the image, never in the trading path). Nine phases: `environment` → `connections` → `policy_failfast` → `universe` → `cap` → `confirmations` → `abort_without_flatten` → `metrics_alerts` → `full_chain` (opt-in). It is sandbox-only by construction: when `create_execution_client()` resolves to the real contour it stops with `DRILL_BLOCKED` (exit 3) before touching anything. Outside `--full-chain` the four mutating broker methods (`execute_order`, `cancel_order`, `post_stop_order`, `cancel_stop_order`) are shadowed **on the instance** by counting guards verified by marker (never by a call-probe against a real client), the operator answer is hard-wired to `n`, and nothing is written to the database. `--full-chain` additionally requires `--i-understand-sandbox-order`, refuses to start when `live_kill_switch` is ON or any `pending`/`open` row exists, places exactly one `CANARY_MAX_LOTS`-lot order through the shipped `process_signal`, verifies the protection at the broker and never flattens on its own (`--cleanup` flattens a *completed* entry through the shipped shutdown path, never after an abort). Exit codes: `0` `DRILL_OK` / `1` `DRILL_FAIL` / `3` `DRILL_BLOCKED`.

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

**Known limitations.**

- The confirmations are stdin-only: a detached start, `nohup`, `docker compose exec` without `-T` or any pipe answers EOF, and EOF is "no". A canary therefore cannot run as a background service - that is the point, not a bug.
- `CANARY_CONFIRM_MAX_RETRIES=3` is a module constant, not a knob. After three `retry` answers the pause gives up: the first one drops the entry, the second one aborts the stream. An operator who cannot arm the protection in three tries needs runbook C of §46.11, not another prompt.
- The cap is a ceiling in **lots**, not in notional: `MAX_POSITION_SIZE` and the rest of `LIVE_RISK` apply unchanged (decision D3), so a canary on an expensive name is still limited by the ordinary risk gates - and the cap never widens a size the sizer already cut.
- The canary is a mode of the executor process, not persisted state: after a restart the runbook must set `CANARY_ENABLED` again, and an ordinary loop keeps publishing `canary_enabled=false` with `ticker` / `max_lots` = `null`.
- `full_chain` is the only code path in the drill that may place an order and it needs two explicit flags plus a clean account; the other eight phases cannot mutate the broker or the database at all.

**Tests.** `cd backend && python -m pytest -q tests/test_live_executor.py -k "canary or confirm or abort or cap"` (shipped defaults and the isolated copy, env overrides plus every fail-fast branch, blank-means-unset, ticker normalisation, caller-dict precedence over env, the 1-lot cap and "only ever shrinks", `min(canary, risk)` open positions, universe narrowing and its fail-closed branch, the direct-call `canary_universe` refusal, both pauses including a raising `confirm_fn` and a closed stdin, the retry limit, the re-arm of the missing leg only, the abort without flatten and its forced-OFF shutdown flag, the counters in the snapshot, the alert lines) plus `tests/test_live_alerting.py` for the `canary_*` snapshot fields and the `canary` section of `GET /api/live-trading/metrics`.

