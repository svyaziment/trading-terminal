# Agent Handover Guide: Trading Terminal

Last refreshed: 2026-08-17 (Issues #59-#62 Live Trading Infrastructure). Companion to project-context.md.
This file is the operational guide for agents. Read project-context.md first for architecture.

## 1. Purpose

Operational knowledge to work on this project safely: structure, DB schema, pipeline, API, known issues, roadmap, operational gotchas, and the collaboration protocol (context collection before multi-element tasks).

## 2. Project Structure

See project-context.md section 2 for the full tree. Key operational entry points:
- `backend/app/main.py` - FastAPI app + route registration.
- `backend/app/analytics/trading_config.py` - trading universe + strategy registry (single source of truth).
- `start_processes.sh` / `stop_processes.sh` - paper trading and opt-in sandbox execution processes.
- `docs/refresh/context_collector.py` - context collector for agent tasks.

## 3. Database Schema

See project-context.md section 3. New tables include `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `live_positions`, `trading_universe`, and `alerts`. All are in schema `trading`.

## 4. Data Pipeline

See project-context.md section 4. Four paper processes are started by default:
1. `data_refresher` - MOEX 1min + aggregation + indicators + signals (every 15 min, top-15).
2. `online_data` - streaming 1min candles + order book.
3. `live_engine` - reads active strategy from DB (`paper_strategy.get_active_paper_strategy`), builds 4h context via `build_strategy_context`, feeds live 1min bars into per-ticker `StrategyEvaluator` instances (unified entry logic, same as backtest), emits signals to `trading.alerts`.
4. `paper_trader` - reads strategy config from DB (RR from `config.risk_reward`), alerts -> market positions (open at best_ask, single arm) -> monitor stop/take -> write equity. Records `strategy_name` in `paper_positions`.
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
- Diagnostics: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Tests: `cd backend && python -m pytest -q tests/test_live_executor.py`.
