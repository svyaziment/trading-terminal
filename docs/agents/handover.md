# Agent Handover Guide: Trading Terminal

Last refreshed: 2026-08-30 (Issue #127 levels_sr_support support-only engine). Companion to project-context.md.
This file is the operational guide for agents. Read project-context.md first for architecture.

## 1. Purpose

Operational knowledge to work on this project safely: structure, DB schema, pipeline, API, known issues, roadmap, operational gotchas, and the collaboration protocol (context collection before multi-element tasks).

## 2. Project Structure

See project-context.md section 2 for the full tree. Key operational entry points:
- `backend/app/main.py` - FastAPI app + route registration.
- `backend/app/analytics/trading_config.py` - trading universe, live top-5, and strategy registry (single source of truth).
- `start_processes.sh` / `stop_processes.sh` - paper trading and opt-in sandbox execution processes.
- `docs/refresh/context_collector.py` - context collector for agent tasks.

## 3. Database Schema

See project-context.md section 3. New tables include `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `live_positions`, `trading_universe`, and `alerts`. All are in schema `trading`.

## 4. Data Pipeline

See project-context.md section 4. Four paper processes are started by default:
1. `data_refresher` - MOEX 1min + aggregation + indicators + signals (every 15 min, top-15).
2. `online_data` - streaming 1min candles + order book.
3. `live_engine` - reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
4. `paper_trader` - reads strategy config from DB (RR from `config.risk_reward`), alerts -> market positions (open at best_ask, single arm) -> monitor stop/take -> write equity and best-effort Telegram notifications. Records `strategy_name` in `paper_positions`.
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
- **JSON NaN / Infinity**: pandas produces NaN/NaT, and a single winning trade yields `pf: Infinity`. Python `json.dumps` writes non-strict JSON that PostgreSQL JSONB rejects (`invalid input syntax for type json`). Sanitize API responses **and** `backtest_results` INSERTs via `_json_dumps` → `_json_safe` in `strategy_jobs.py` (`inf`/`nan` → `null`). Also used for API payloads in `paper_trading_jobs.py`. Cast timestamps to text in SQL (`created_at::text`).
- **JSONB as string**: DBManager returns JSONB columns as Python-repr strings, not dicts. Normalize with `_to_dict` (json.loads, then ast.literal_eval fallback).
- **Backtest matrix runtime**: full matrix takes ~10-15 min. Use quick=true for liveness.
- **Reports mount**: backend mounts `./reports` (docker-compose). Strategy runs write `reports/strategy-lab/last_run.json` - send it on any Strategy Lab error.
- **Resistance-zone veto (Issue #97)**: `levels_reversal` must not enter when the 1min close sits in an active resistance zone, even if `nearest_level_at(..., 'support')` returns a valid support and the 0.5×ATR extension covers the fill. That is a structural defect, not role-reversal (ALRS paper #711: fill 19.80 inside impulse resistance 19.67). Guard: `overlapping_resistance_zone_at` in `StrategyEvaluator.check_entry`. Issue #106: the same function skips non-`active` zones when a `state` column is present. Issue #107: `StrategyEvaluator` passes `LevelsTracker` (and `is_broken`) into the veto only when `level_breakout_retest` is enabled; locked `test_20260731` does not, so paper/live veto is unchanged. Do not rewrite locked `test_20260731`. Units: `cd backend && python -m pytest -q tests/test_resistance_zone_veto.py tests/test_levels_state_machine.py tests/test_level_breakout_retest.py`.
- **Lab plugin HTF (Issue #116)**: Strategy Lab always saves `config.strategy_name = "levels_reversal"`, so `_run_job` calls `run_portfolio_backtest`, not `run_strategy_backtest`. `MarketContext.htf_bars` must carry `build_strategy_context()['htf_bars']` (same TF as the levels). `candles_4h` is usually unset on this path. Without HTF, `LevelsTracker._sync_tracker` exits immediately, every level stays `active`, and any breakout pattern (`level_breakout_retest`, `levels_sr_breakout`) yields **zero trades**. Locked `test_20260731` does not enable breakout, so wiring HTF is a no-op for paper. Units: `cd backend && python -m pytest -q tests/test_strategy_plugin.py tests/test_level_breakout_retest.py`.
- **Composite S/R (Issue #117)**: `levels_sr_breakout` is an **entry engine** (OR of path A support and path B resistance retest), not an AND-filter. Isolated Lab run: `config.patterns` has `levels_sr_breakout` (and optionally `signal_4h_buy`) **without** `levels_reversal`. If both chips are on, the composite wins (one support path). Do not AND with `level_breakout_retest` as a replacement. Trades carry `source` (`levels_sr_breakout_support` / `levels_sr_breakout_resistance`). Locked `test_20260731` must stay off this id. Units: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.
- **AFKS smoke (Issue #119)**: isolated ticker backtest, not a 50k portfolio. Package `analytics/issue-119-afks-sr-breakout-smoke/`. A vs B on AFKS `2024-08-01`…`< 2026-08-21`. B-support n can exceed A because the composite passes `LevelsTracker` into the veto. `run_strategy_backtest` is the source of `source`; `run_portfolio_backtest` currently drops it. Do not lock/overwrite `test_20260731` / `test_20260820` / `test_20260821`. Replay: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py` (needs `results.json`). Units: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.
- **Lab-universe A/B (Issue #124)**: isolated 28-ticker `get_big_tickers` run, same SHA as #119. Package `analytics/issue-124-sr-breakout-universe/`. A n=2559 PF 1.46; B n=4799 PF 1.39 (support 3811 / resistance 988). AFKS matched #119; ALRS 19.80 bar absent. Optional 50k slot replay of B is a separate block, not isolated PF. Do not lock/overwrite the three reference strategies. Replay: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Units: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.
- **Support with tracker (Issue #127 / Epic #126)**: `levels_sr_support` is the **B-support-only** entry engine from #124 (PF 1.51, n=3811). Same support geometry as `levels_reversal` plus the #97 veto **with** `LevelsTracker`. No `check_breakout_retest`. Isolated run: `config.patterns` has `levels_sr_support` (optionally `signal_4h_buy`) **without** `levels_reversal` / `levels_sr_breakout` / `level_breakout_retest`. Composite still wins if both engines are on. Do not silently turn the tracker on for locked `test_20260731`. Units: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_strategy_plugin.py`.

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
- Safe start: `START_LIVE_EXECUTOR=1 ./start_processes.sh`. This additional opt-in prevents the normal paper workflow from placing sandbox broker orders. Logs: `reports/live-executor/executor.log`.
- Processing order is fixed: `StrategyEvaluator` BUY -> fresh imbalance -> free RUB -> position sizing -> market BUY -> take sell-limit -> DB record/reconciliation.
- Stop protection is synthetic. Never place the stop sell-limit at entry: a sell-limit below the market executes immediately. The monitor waits for `current_price <= stop_price`, cancels take, then submits a sell-limit at the observed price.
- Every physical broker attempt, including SDK retries and account discovery, shares one token bucket (`api_rate_limit`, maximum 10/sec). Do not add independent broker calls outside `_broker_call` or bypass the client's `before_request` hook.
- SIGTERM/SIGINT requests cleanup. Pending entry and protection orders are cancelled; open holdings are flattened only when `close_positions_on_shutdown=true`. With the default false value, holdings remain open and their protection IDs are cleared in DB.
- Read rejected BUY diagnostics in `reports/live-executor/executor.log`. Each `Live signal skipped` record contains `ticker=<ticker>`, a stable `reason=<code>`, and relevant values. Expected filter/capacity codes are `stale_or_missing_orderbook`, `imbalance_below_threshold`, `insufficient_cash`, `invalid_stop`, `insufficient_capital`, `max_open_positions`, and `broker_error`; `min_lot` remains executable under the sizing contract. For example, `reason=imbalance_below_threshold imbalance=0.9 imbalance_threshold=1.0` means the stream is live but the filter rejected entry, while `reason=stale_or_missing_orderbook orderbook_age_seconds=missing` indicates absent book data. These records are emitted only after `StrategyEvaluator` produces a BUY decision; no skip records can simply mean that no BUY signal was generated. Broker errors log only the operation and exception type, never credentials, account details, or exception text.
- Diagnostics: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Tests: `cd backend && python -m pytest -q tests/test_live_executor.py`.

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

- Paper/data refresh keep `get_trading_universe()` (top-15 from `trading.trading_universe`).
- Sandbox execution uses `LIVE_UNIVERSE` / `get_live_trading_universe()`: SBER, LKOH, RUAL, NVTK, GAZP (Issue #66). `LiveExecutor.initialize()` intersects paper-strategy tickers with this list.
- Do not shrink the DB table to five names: that would stop streaming and paper trading on the rest of the top-15.
- Ranking code and report: `analytics/issue-66-live-universe/`. Re-run `analysis.py` after a new `extract_inputs.py` snapshot; keep `LIVE_UNIVERSE` in sync with `summary.json`.
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
- Isolated AFKS smoke (Issue #119): handover §27. Lab-universe A/B (Issue #124): handover §28. Support-only engine (Issue #127): handover §29. Do not treat either package as a paper verdict.

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
- Lab schema: `PATTERN_REGISTRY['levels_sr_support']` (also on `SIGNAL_ENGINE_PATTERN_SCHEMAS` for `GET /api/patterns`). Category `levels` (next to `levels_reversal` and `levels_sr_breakout`, not in breakout). Icon `support_tracker` (must stay distinct from `breakout_up` and `support_breakout`). Params = **only** `levels_reversal` fields — no retest keys. Lab chip is Issue #128. Do not hardcode the param keys in TSX.
- Isolated run: `config.patterns` contains `levels_sr_support` and optionally `signal_4h_buy` / SignalEngine ids. `levels_reversal` is **not** required. `run_strategy_backtest` treats this id as a sufficient entry engine.
- Order in `check_entry` after session / HTF / `_sync_tracker`: (1) if `levels_sr_breakout` is on, the composite wins (unchanged); (2) else if `levels_sr_support` is on — common AND, then support zone + confirm + veto of *active* resistance with `tracker=self._tracker` → `source=levels_sr_support`, levels stop/take, top-level RR filter. **Do not** call `check_breakout_retest` on this id.
- Both chips (`levels_sr_support` + `levels_sr_breakout`): composite wins. `levels_sr_support` + `levels_reversal`: the new id wins (one support path, no doubling). `_LEVELS_CONFIRM_PATTERN_IDS` order is composite > support-with-tracker > `levels_reversal`.
- Difference vs `levels_reversal`: tracker is passed into the veto, so a broken resistance no longer blocks a valid support entry. Difference vs `levels_sr_breakout`: no path B / retest.
- Tracker / `htf_bars`: same feed as #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit tests do not depend on the Lab UI.
- Locked `test_20260731` must not enable this id (paper/live veto and levels stop/take stay bit-for-bit).
- Unit tests: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_levels_sr_breakout.py tests/test_strategy_plugin.py`.

