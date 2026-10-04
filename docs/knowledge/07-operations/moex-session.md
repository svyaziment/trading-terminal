# Real-time Order-book Imbalance

> **Source:** project-context.md sections 11 + handover.md sections 12
> **Last refreshed:** 2026-10-04, task-346

## 11. Real-time Order-book Imbalance

`backend/app/analytics/orderbook_imbalance.py` is the shared calculator and mandatory live-entry filter for Issue #60. On every streamed order-book update, `online_data.py` sums quantities over the configured first 10 bid and ask levels and persists:

`volume_imbalance = bid_depth / ask_depth`

Infrastructure defaults live in `trading_config.py` (`ORDERBOOK_IMBALANCE`): depth 10, maximum aggregate age 5 minutes, and default threshold 1.0. The active strategy may override only the top-level `imbalance_threshold`; a live entry passes when its finite imbalance is strictly above that threshold.

`live_engine.py` recalculates the ratio from `bid_depth` and `ask_depth` in the latest fresh `trading.online_orderbook_aggregates` row before emitting every signal. Missing/stale rows, null/non-finite values, and zero ask depth all produce `None`, so the mandatory filter rejects the signal rather than silently using zero or stale data. The legacy online signal path uses the same calculator.

## 12. Operating the Order-book Imbalance Filter

- Entry points: `online_data.save_orderbook_aggregate` calculates and stores each stream update; `orderbook_imbalance.get_recent_imbalance` reads a fresh aggregate; `passes_imbalance_filter` is the mandatory signal gate.
- Infrastructure policy is `ORDERBOOK_IMBALANCE` in `trading_config.py`: depth 10, maximum age 5 minutes, default threshold 1.0. Strategy override: top-level `config.imbalance_threshold`.
- Passing condition is strict: `volume_imbalance > imbalance_threshold`. Missing, stale, null, NaN/infinite data, or zero ask depth always rejects the signal.
- Quick DB diagnostic:
  `SELECT ticker, timestamp, bid_depth, ask_depth, volume_imbalance FROM trading.online_orderbook_aggregates ORDER BY timestamp DESC LIMIT 20;`
- If all live signals are skipped, first confirm that `online_data` is running and the latest row is less than 5 minutes old. Do not weaken the missing-data guard.
- Unit test: `cd backend && python -m pytest -q tests/test_orderbook_imbalance.py`.
