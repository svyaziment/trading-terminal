# Database Schema (PostgreSQL, schema: trading)

> **Source:** project-context.md sections 3 + handover.md sections 42
> **Last refreshed:** 2026-10-04, task-346

## 3. Database Schema (PostgreSQL, schema: trading)

| Table | Rows (approx) | Description |
|---|---|---|
| candles_30min_raw | ~28k | 30min candles from T-Bank API (30 tickers, ~1 month) |
| candles_1min_raw | ~14.6M | 1min candles from MOEX ISS API (top-15+, 2 years) |
| candles_aggregated | ~384k | Aggregated candles (30min, 1h, 4h, 1d) |
| indicators | ~210k | 33 technical indicators per candle |
| signals | ~170k | BUY/SELL signals (10 patterns, confidence, total_signals) |
| instruments | ~4.3k | Ticker metadata (figi, lot_size, min_price_increment) |
| top_stocks_by_volume | 30 | Top 30 tickers by volume |
| online_candles_1min | streaming | Live 1min candles (streaming) |
| online_orderbook_aggregates | streaming | Live order book aggregates (bid/ask depth, volume_imbalance) |
| **strategies** | ~15 | Strategy Lab: name, config (jsonb), in_paper_test, locked, description |
| **backtest_results** | ~64 | Strategy Lab: per-ticker backtest/walk-forward metrics (jsonb) |
| **paper_positions** | ~704 | Paper trading positions (A/B factors, limit/market, stop/take, PnL) |
| **paper_equity** | ~2855 | Equity curve (equity_rub, realized_pnl, drawdown_pct, open_positions) |
| **live_positions** | runtime | T-Bank Sandbox orders, protection IDs, lifecycle, and realized PnL |
| **live_equity** | runtime | Per-cycle live account equity snapshot: cash + market value, daily/all-time peak, drawdown, risk-breach flag (Issue #176) |
| **trading_universe** | 15 | Traded universe (ticker, rank, pf, source) - top-15 by PF |
| **alerts** | ~72 | Online signals (details jsonb: price, support/take, factors) |
| backtest_runs | ~300 | Backtest run metadata (legacy + levels matrix) |
| backtest_trades | ~200k | Individual trades (legacy matrix) |
| backtest_equity | ~200k | Equity curve per run (legacy matrix) |
| backtest_metrics | ~6.3k | Aggregated metrics per run/group (PF, expectancy, win_rate, benchmarks) |

Key columns (new tables):
- `strategies`: id, name (unique), config (jsonb: patterns, confirm_windows, commission_pct, slippage_pct, risk_reward, trailing_stop, n_runs), in_paper_test (bool), locked (bool), description. `trailing_stop` is the top-level stepped-trailing exit block of Issue #144 (§6, no schema migration, default OFF), applied by the backtest engine since Issue #145 (§19) — the other keys keep their pre-#144 meaning.
- `backtest_results`: id, strategy_id (FK), ticker, test_type (full_sample/walkforward), depth, metrics (jsonb), created_at
- `paper_positions`: id, ticker, entry_ts/price, stop_price, take_price, limit_price, limit_ts, size_lots, size_rub, lot_size, status (pending/open/closed_stop/closed_take/cancelled), signal_source, window_mode, rr_mode, rr_ratio, entry_mode (market/limit), signal_id, strategy_name, exit_ts/price/reason, pnl_rub, pnl_pct
- `live_positions`: id, ticker, instrument_id, signal_ts, entry_price, lot_size, size_lots, stop_price, take_price, broker_order_id/stop_id/take_id, status, strategy_name, exit_ts/price/reason, pnl_rub
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `live_equity`: id, timestamp (naive MSK), session_key (MSK calendar day), equity_rub, cash_rub, market_value_rub, realized_pnl_rub, unrealized_pnl_rub, peak_equity_rub (daily peak), peak_equity_all_time_rub, drawdown_pct, open_positions, risk_breach, account_id, strategy_name, created_at. `paper_equity` and its `write_equity` writer are untouched (Issue #176).
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

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
