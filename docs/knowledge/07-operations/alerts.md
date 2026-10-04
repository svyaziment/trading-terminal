# Telegram Alerting

> **Source:** project-context.md sections 14 + handover.md sections 16
> **Last refreshed:** 2026-10-04, task-346

## 14. Telegram Alerting

`backend/app/notifications/telegram_notifier.py` sends Markdown messages through the Telegram Bot API. It uses `TGM_TOKEN` and `TGM_CHAT`; legacy `TGM_CHAT_ID` remains a fallback. `TGM_APP_ID` and `TGM_APP_HASH` are loaded for configuration compatibility but are not required by the Bot API.

Delivery is serialized and limited to one attempt per second. Network/API errors are logged and returned as `False`, never propagated into the trading loop. `paper_trader.py` emits alerts after successful DB writes for market and limit opens and for every close (including stop/take). Equity updates emit a critical alert only when drawdown first crosses `risk.max_daily_loss_pct`, or when equity first reaches zero (GAME OVER), preventing repeated alerts on every loop.

Since #177 (Epic #172 task E) the **live contour** uses the same notifier: `LiveExecutor._notify()` sends event alerts (start/stop, entry, exit with slippage, stop ratchet, `protection_failed`, invariant violations, OCO orphans, `risk_breach` and its recovery, kill switch, consecutive-error threshold) plus a periodic "the loop is alive" heartbeat. The policy lives in the `LIVE_ALERTING` section of `trading_config.py`, the credentials are unchanged (`TGM_TOKEN` / `TGM_CHAT_ID` through `config_manager.load_settings().telegram`), and a missing token keeps every event in the log only. Details: §23 and `handover.md` §45.

## 16. Operating Telegram Paper Alerts

- Entry point: `app.notifications.telegram_notifier.TelegramNotifier`; paper-trading integration lives in `paper_trader.py`.
- Set `TGM_TOKEN` and `TGM_CHAT`; legacy `TGM_CHAT_ID` remains a fallback. `TGM_APP_ID` and `TGM_APP_HASH` are loaded but not used by the Bot API. Never print or commit these values.
- The notifier sends Markdown open/close messages with ticker, BUY/SELL, price, lot and unit counts, PnL, and reason. Stop/take use distinct icons; critical events use 🚨.
- Calls are serialized at one attempt per second. Delivery errors are warnings only and must never terminate paper trading.
- Large-drawdown alerts use `risk.max_daily_loss_pct` and fire only on threshold crossing. GAME OVER fires only on the first transition to non-positive equity.
- Tests: `cd backend && python -m pytest -q tests/test_telegram_notifier.py tests/test_paper_trader_notifications.py`.
