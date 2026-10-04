# Live Trading Monitoring Panel

> **Source:** project-context.md sections 15 + handover.md sections 17
> **Last refreshed:** 2026-10-04, task-346

## 15. Live Trading Monitoring Panel

`frontend/src/components/LiveTradingPanel.tsx` is available from the `Live Trading` tab. It polls `trading.live_positions` through the live monitoring API every 10 seconds and shows open positions with the latest best bid (best ask fallback), unrealized RUB/% PnL, paginated and sortable trade history, cumulative realized PnL, and Telegram connectivity. Both tables use the shared `ui/DataTable` and `FilterChips`; date filters use the shared `ui/DatePicker` extracted from Strategy Lab.

`/api/live-trading/positions` and `/api/live-trading/dynamics` keep sandbox execution data separate from paper trading. They support ticker/date/status filters; the special `status=closed` value selects stop, take, and trailing closures (#149). `/api/notifications/status` performs a read-only Telegram `getMe` probe and caches the result for 30 seconds. It never returns credentials. See `docs/strategy/live-trading.md` for the full contour (safety contract, synthetic stop, schema-ready trailing columns).

Since #177 the panel also has a source for the executor's own health: `GET /api/live-trading/metrics` serves the persisted `LiveExecutor` snapshot (loop state, heartbeat age, error and alert counters, risk status, kill switch, open positions with and without broker protection) and is meant for a status tile and for external monitoring. The endpoint does not require a running executor and degrades to `available=false` + `reason` instead of a 500 (§23).

## 17. Operating the Live Trading Panel

- Open the `Live Trading` frontend tab. It polls sandbox data from `trading.live_positions`, PnL dynamics, and Telegram status every 10 seconds.
- Open-position `current_price` comes from the latest order-book best bid, with best ask as fallback. Missing market data is rendered as unavailable rather than using a stale hardcoded price.
- Both tables use the shared `DataTable` and filter chips used by Strategy Lab. Filters open from column headers, and date ranges use the shared calendar `DatePicker`. History retains server-side sorting and pagination; no exact status filter means all closed positions (`closed_stop` and `closed_take`).
- `/api/notifications/status` performs Telegram `getMe` without sending a message and caches the result for 30 seconds. `configured=false` means `TGM_TOKEN` or chat ID is absent; `configured=true` with `disconnected` means the Bot API probe failed.
- Frontend check: `cd frontend && npm run build`. Backend checks: `cd backend && python -m pytest -q tests/test_live_trading_api.py tests/test_notifications_api.py tests/test_telegram_notifier.py`.
