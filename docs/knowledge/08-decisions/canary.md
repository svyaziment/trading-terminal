# First Sandbox LiveExecutor Canary

> **Source:** handover.md sections 19
> **Last refreshed:** 2026-10-04, task-346

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
