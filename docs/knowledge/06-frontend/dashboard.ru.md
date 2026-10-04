# Панель мониторинга Live Trading

> **Source:** project-context.ru.md sections 15 + handover.ru.md sections 17
> **Last refreshed:** 2026-10-04, task-346

## 15. Панель мониторинга Live Trading

`frontend/src/components/LiveTradingPanel.tsx` доступна во вкладке `Live Trading`. Панель каждые 10 секунд читает `trading.live_positions` через live monitoring API и показывает открытые позиции с последним best bid (fallback на best ask), нереализованный PnL в RUB/%, пагинируемую и сортируемую историю сделок, накопленный realized PnL и подключение Telegram. Обе таблицы построены на общем `ui/DataTable`, разделяют `FilterChips`, а фильтры дат используют вынесенный из Strategy Lab общий `ui/DatePicker`.

`/api/live-trading/positions` и `/api/live-trading/dynamics` отделяют данные sandbox-исполнения от paper trading. Эндпоинты поддерживают фильтры тикера, дат и статуса; специальное значение `status=closed` выбирает закрытия по stop, take и trailing (#149). `/api/notifications/status` выполняет read-only проверку Telegram `getMe` и кеширует результат на 30 секунд. Реквизиты в ответ не попадают. Полный контур (контракт безопасности, синтетический стоп, готовые trailing-колонки схемы) — в `docs/strategy/live-trading.ru.md`.

С #177 к панели добавился источник здоровья самого исполнителя: `GET /api/live-trading/metrics` отдаёт персистентный снимок `LiveExecutor` (состояние цикла, возраст пульса, счётчики ошибок и алертов, риск-статус, kill switch, открытые позиции с защитой и без) и предназначен для плитки статуса и для внешнего мониторинга. Эндпоинт не требует запущенного исполнителя и деградирует в `available=false` + `reason`, а не в 500 (§23).

## 17. Эксплуатация панели Live Trading

- Откройте вкладку frontend `Live Trading`. Sandbox-данные из `trading.live_positions`, динамика PnL и статус Telegram обновляются каждые 10 секунд.
- `current_price` открытой позиции берётся из последнего best bid стакана с fallback на best ask. При отсутствии рыночных данных UI показывает недоступное значение, а не подставляет устаревшую цену.
- Обе таблицы используют единый `DataTable` и общие filter chips, как в Strategy Lab. Фильтры открываются в заголовках; диапазоны дат используют общий календарный `DatePicker`. История поддерживает серверную сортировку и пагинацию; отсутствие точного фильтра статуса означает все закрытые позиции (`closed_stop` и `closed_take`).
- `/api/notifications/status` выполняет Telegram `getMe` без отправки сообщения и кеширует результат на 30 секунд. `configured=false` означает отсутствие `TGM_TOKEN` или chat ID; `configured=true` вместе с `disconnected` означает ошибку проверки Bot API.
- Проверка frontend: `cd frontend && npm run build`. Backend: `cd backend && python -m pytest -q tests/test_live_trading_api.py tests/test_notifications_api.py tests/test_telegram_notifier.py`.
