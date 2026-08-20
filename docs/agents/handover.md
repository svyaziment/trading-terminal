# Agent Handover Guide: Trading Terminal

Last refreshed: 2026-08-20 (Issue #97 ALRS resistance-zone veto). Companion to project-context.md.
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
- **JSON NaN**: pandas produces NaN/NaT that `json.dumps` rejects ("Out of range float values"). Sanitize API responses (see `_json_safe` in strategy_jobs.py / paper_trading_jobs.py) and cast timestamps to text in SQL (`created_at::text`).
- **JSONB as string**: DBManager returns JSONB columns as Python-repr strings, not dicts. Normalize with `_to_dict` (json.loads, then ast.literal_eval fallback).
- **Backtest matrix runtime**: full matrix takes ~10-15 min. Use quick=true for liveness.
- **Reports mount**: backend mounts `./reports` (docker-compose). Strategy runs write `reports/strategy-lab/last_run.json` - send it on any Strategy Lab error.
- **Resistance-zone veto (Issue #97)**: `levels_reversal` must not enter when the 1min close sits in an active resistance zone, even if `nearest_level_at(..., 'support')` returns a valid support and the 0.5×ATR extension covers the fill. That is a structural defect, not role-reversal (ALRS paper #711: fill 19.80 inside impulse resistance 19.67). Guard: `overlapping_resistance_zone_at` in `StrategyEvaluator.check_entry`. Do not rewrite locked `test_20260731`. Unit: `cd backend && python -m pytest -q tests/test_resistance_zone_veto.py`.

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
