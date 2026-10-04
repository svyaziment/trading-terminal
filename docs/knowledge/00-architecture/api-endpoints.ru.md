# API Endpoints

> **Source:** project-context.ru.md sections 5
> **Last refreshed:** 2026-10-04, task-346

## 5. API Endpoints

| Метод | Путь | Описание |
|---|---|---|
| GET | /health | Проверка работоспособности |
| GET | /api/candles | Свечи (ticker, timeframe, limit) |
| GET | /api/instruments | Список инструментов |
| GET | /api/top-stocks-by-volume | Топ 30 по объёму |
| GET | /api/signals | Сигналы (ticker, timeframe, limit, filters, pagination) |
| GET | /api/signals/stats | Статистика сигналов |
| POST | /api/data/refresh | Фон: загрузка + агрегация + индикаторы + сигналы (shared lock) |
| POST | /api/signals/regenerate | Фон: перегенерация сигналов (shared lock) |
| GET | /api/jobs/status | Статус всех задач |
| POST | /api/backtest/run | Фон: legacy pattern matrix backtest |
| POST | /api/levels-backtest/run | Матрица levels backtest |
| GET | /api/patterns | Схемы реестра паттернов (Strategy Lab) |
| GET | /api/strategies/trailing-schema | Контракт `config.trailing_stop` для редактора Lab: дефолты, границы, `max_steps`, разрешение ввода, имя утверждённой сетки, коды причин (#146; только чтение, отдаёт `trading_config.get_trailing_stop_schema()`) |
| POST | /api/patterns/preview | Превью паттерна на графике: свечи + overlays (`ray`, `band`, `line`, `marker`); #88 — `levels_reversal` |
| POST | /api/strategies | Сохранить стратегию (отклоняет перезапись locked; #149: валидация `trailing_stop` → 422 с `reason_codes`) |
| GET | /api/strategies | Список стратегий (with in_paper_test/locked/description; #149: + `trailing_stop` metadata) |
| GET | /api/strategies/run/status | Статус задачи backtest стратегии |
| GET | /api/strategies/data-range | Мин./макс. дата candles_1min_raw (для date pickers) |
| POST | /api/strategies/{id}/run | Запустить backtest (full_sample/walkforward, depth или кастомные date_from/date_to) |
| GET | /api/strategies/{id}/results | Результаты backtest (метрики по тикерам) |
| GET | /api/tickers/big | Тикеры с >= N 1min свечей (выбираемая вселенная) |
| GET | /api/paper-trading/overview | Имя стратегии + опции факторов + сводная статистика (фильтры факторов; #149: + `trailing_closed`, `trailing_closed_pnl_rub`, `trailing_open`, `active_stop_count`) |
| GET | /api/paper-trading/positions | Список позиций (фильтры + пагинация + сортировка); открытые строки содержат текущую цену и нереализованный PnL; #149: + `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`; `status=closed` включает `closed_trailing` |
| GET | /api/paper-trading/dynamics | Кумулятивный ряд реализованного PnL с шагом 1h/1d/1w (фильтры факторов/тикера/дат) |
| GET | /api/notifications/status | Кешированный статус конфигурации и подключения Telegram Bot API |
| GET | /api/live-trading/positions | Sandbox live-позиции с текущей ценой, PnL, фильтрами, сортировкой и пагинацией; #149: + `trailing_enabled`, `current_stop_price`, `step_reached`, `risk_r`; `status=closed` включает `closed_trailing` |
| GET | /api/live-trading/dynamics | Кумулятивный sandbox PnL с шагом 1h/1d/1w |
| GET | /api/live-trading/equity/current | Последний снимок `live_equity` + `risk_breach_active` + действующие лимиты и их валидированные границы, чтобы панель не хардкодила числа (#176) |
| GET | /api/live-trading/equity/latest | Алиас `/equity/current` (#176) |
| GET | /api/live-trading/equity/history | Кривая эквити live, свежие первыми; фильтры `session_key`, `date_from`, `date_to`; пагинация (#176). 503 с подсказкой `alembic upgrade head`, если таблицы нет |
| GET | /api/live-trading/metrics | Снимок метрик `LiveExecutor` из `trading.app_settings['live_executor_metrics']`: `state` (unknown/kill_switch/no_heartbeat/stale/error_threshold/risk_breach/running), возраст снимка и пульса, счётчики цикла/защиты/риска/алертов, kill switch из живой строки, открытые позиции с защитой и без (#177). Деградирует `available=false` + `reason` вместо 500 |
| POST | /api/live-trading/kill-switch | Глобальный аварийный останов live-контура (#178). Тело `{"enabled": bool, "reason"?: str<=200}`; upsert `trading.app_settings.live_kill_switch` с подтверждением чтением обратно (`ok`/`confirmed`) и `503` с именем миграции `20260928_001`, если таблица недоступна. Входы отклоняются с причиной `kill_switch`, открытые позиции сохраняют брокерские стопы |

Общий lock: jobs_state.py (in-process). Одновременно выполняется только одна тяжёлая задача; остальные возвращают 409.
