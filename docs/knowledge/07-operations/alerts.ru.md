# Telegram alerting

> **Source:** project-context.ru.md sections 14 + handover.ru.md sections 16
> **Last refreshed:** 2026-10-04, task-346

## 14. Telegram alerting

`backend/app/notifications/telegram_notifier.py` отправляет Markdown-сообщения через Telegram Bot API. Используются `TGM_TOKEN` и `TGM_CHAT`; прежнее имя `TGM_CHAT_ID` сохранено как fallback. `TGM_APP_ID` и `TGM_APP_HASH` загружаются для совместимости конфигурации, но Bot API их не требует.

Отправка сериализована и ограничена одной попыткой в секунду. Сетевые ошибки и ошибки API логируются и возвращают `False`, но не попадают в торговый цикл. `paper_trader.py` отправляет alerts после успешной записи в БД для market/limit открытий и каждого закрытия, включая stop/take. При обновлении equity критический alert отправляется только при первом пересечении `risk.max_daily_loss_pct` или первом достижении нулевого equity (GAME OVER), поэтому каждый цикл не создаёт повторное сообщение.

С #177 (эпик #172, блок E) тот же нотари использует и **live-контур**: `LiveExecutor._notify()` отправляет событийные алерты (старт/стоп, вход, выход с проскальзыванием, перенос стопа, `protection_failed`, нарушение инвариантов, OCO-сироты, `risk_breach` и его снятие, kill switch, порог ошибок подряд) плюс периодический heartbeat «процесс жив». Политика — в секции `LIVE_ALERTING` (`trading_config.py`), креденшелы прежние (`TGM_TOKEN` / `TGM_CHAT_ID` через `config_manager.load_settings().telegram`), а отсутствие токена оставляет события только в логе. Подробности — §23 и `handover.ru.md` §45.

## 16. Эксплуатация Telegram alerts для paper trading

- Точка входа: `app.notifications.telegram_notifier.TelegramNotifier`; интеграция с paper trading находится в `paper_trader.py`.
- Задайте `TGM_TOKEN` и `TGM_CHAT`; прежнее имя `TGM_CHAT_ID` сохранено как fallback. `TGM_APP_ID` и `TGM_APP_HASH` загружаются, но Bot API их не использует. Никогда не печатайте и не коммитьте эти значения.
- Notifier отправляет Markdown-сообщения об открытии/закрытии с тикером, BUY/SELL, ценой, количеством лотов и штук, PnL и причиной. Stop/take имеют отдельные эмодзи, критические события — 🚨.
- Вызовы сериализуются с частотой не более одной попытки в секунду. Ошибки доставки только логируются и не должны останавливать paper trading.
- Alert большого drawdown использует `risk.max_daily_loss_pct` и отправляется только при пересечении порога. GAME OVER отправляется только при первом переходе equity в неположительное значение.
- Тесты: `cd backend && python -m pytest -q tests/test_telegram_notifier.py tests/test_paper_trader_notifications.py`.
