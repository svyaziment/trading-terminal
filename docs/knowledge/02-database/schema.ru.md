# Схема базы данных (PostgreSQL, схема: trading)

> **Source:** project-context.ru.md sections 3 + handover.ru.md sections 42
> **Last refreshed:** 2026-10-04, task-346

## 3. Схема базы данных (PostgreSQL, схема: trading)

| Таблица | Строк (примерно) | Описание |
|---|---|---|
| candles_30min_raw | ~28k | 30min свечи из T-Bank API (30 тикеров, ~1 месяц) |
| candles_1min_raw | ~14.6M | 1min свечи из MOEX ISS API (top-15+, 2 года) |
| candles_aggregated | ~384k | Агрегированные свечи (30min, 1h, 4h, 1d) |
| indicators | ~210k | 33 технических индикатора на свечу |
| signals | ~170k | BUY/SELL сигналы (10 паттернов, confidence, total_signals) |
| instruments | ~4.3k | Метаданные тикеров (figi, lot_size, min_price_increment) |
| top_stocks_by_volume | 30 | Топ 30 тикеров по объёму |
| online_candles_1min | streaming | Живые 1min свечи (streaming) |
| online_orderbook_aggregates | streaming | Живые агрегаты стакана (bid/ask depth, volume_imbalance) |
| **strategies** | ~15 | Strategy Lab: name, config (jsonb), in_paper_test, locked, description |
| **backtest_results** | ~64 | Strategy Lab: метрики backtest/walk-forward по тикерам (jsonb) |
| **paper_positions** | ~704 | Позиции paper trading (A/B factors, limit/market, stop/take, PnL) |
| **paper_equity** | ~2855 | Кривая equity (equity_rub, realized_pnl, drawdown_pct, open_positions) |
| **live_positions** | runtime | Sandbox-ордера T-Bank, идентификаторы защиты, жизненный цикл и PnL |
| **live_equity** | runtime | Снимок эквити live-счёта каждый цикл: кэш + рыночная стоимость, дневной/глобальный пик, drawdown, флаг risk-breach (задача #176) |
| **trading_universe** | 15 | Торговая вселенная (ticker, rank, pf, source) - top-15 по PF |
| **alerts** | ~72 | Онлайн-сигналы (details jsonb: price, support/take, factors) |
| backtest_runs | ~300 | Метаданные прогонов backtest (legacy + levels matrix) |
| backtest_trades | ~200k | Отдельные сделки (legacy matrix) |
| backtest_equity | ~200k | Кривая equity по прогонам (legacy matrix) |
| backtest_metrics | ~6.3k | Агрегированные метрики по прогонам/группам (PF, expectancy, win_rate, benchmarks) |

Ключевые колонки (новые таблицы):
- `strategies`: id, name (unique), config (jsonb: patterns, confirm_windows, commission_pct, slippage_pct, risk_reward, trailing_stop, n_runs), in_paper_test (bool), locked (bool), description. `trailing_stop` — верхнеуровневый блок ступенчатого трейлинг-стопа из задачи #144 (§6, без миграции схемы, по умолчанию выключен), который с задачи #145 применяет боевой движок (§19); остальные ключи сохраняют смысл, который имели до #144.
- `backtest_results`: id, strategy_id (FK), ticker, test_type (full_sample/walkforward), depth, metrics (jsonb), created_at
- `paper_positions`: id, ticker, entry_ts/price, stop_price, take_price, limit_price, limit_ts, size_lots, size_rub, lot_size, status (pending/open/closed_stop/closed_take/cancelled), signal_source, window_mode, rr_mode, rr_ratio, entry_mode (market/limit), signal_id, strategy_name, exit_ts/price/reason, pnl_rub, pnl_pct
- `live_positions`: id, ticker, instrument_id, signal_ts, entry_price, lot_size, size_lots, stop_price, take_price, broker_order_id/stop_id/take_id, status, strategy_name, exit_ts/price/reason, pnl_rub
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `live_equity`: id, timestamp (naive MSK), session_key (календарный день МСК), equity_rub, cash_rub, market_value_rub, realized_pnl_rub, unrealized_pnl_rub, peak_equity_rub (дневной пик), peak_equity_all_time_rub, drawdown_pct, open_positions, risk_breach, account_id, strategy_name, created_at. `paper_equity` и её `write_equity` не затронуты (задача #176).
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 42. Контракт схемы live_positions и fail-fast preflight (задача #173)

### Зачем

Runtime-DDL в `ensure_live_positions_table()` создавал 20 колонок и CHECK из пяти статусов, тогда как Alembic (`20260915_002`, `20260916_001`) требует 30 колонок и семь статусов. Прод-база, созданная исполнителем без миграций, падала бы на трейлинг-колонках (`UndefinedColumn`) и не могла сохранить `closed_trailing` / `closed_broker`. Preflight схему не проверял вовсе, поэтому дрейф всплывал уже во время торговли, а не на старте.

### Что изменилось

| Файл | Изменения |
| --- | --- |
| `backend/app/analytics/live_schema.py` | **Новый модуль**: константы контракта (30 колонок, 7 статусов, ключи `app_settings`), идемпотентный DDL `ensure_live_positions_schema()` (`LIVE_SCHEMA_STATEMENTS`), `inspect_live_schema()`, `validate_live_schema()`, `assert_live_schema()` / `LiveSchemaError`, `describe_live_schema_problems()`, `live_schema_summary()` |
| `backend/app/analytics/live_executor.py` | `ensure_live_positions_table()` — тонкая обёртка над модулем контракта; `initialize()` вызывает `assert_live_schema()` сразу после DDL |
| `backend/app/analytics/live_executor_preflight.py` | Новая блокирующая проверка `live_positions_schema` и блок `details.live_schema` |
| `backend/tests/test_live_schema.py` | **Новый файл**: 23 теста (контракт, идемпотентность и порядок DDL, обнаружение дрейфа, предупреждения, fail-fast исполнителя) |
| `backend/tests/test_live_executor.py` | `FakeDB` отвечает на `information_schema.tables` / `information_schema.columns` / `pg_constraint`; по умолчанию описывает полностью мигрированную схему, аргументы конструктора позволяют имитировать дрейф |

### Поведение

- DDL идемпотентен и является суперсетом обеих миграций: `CREATE TABLE IF NOT EXISTS` (30 колонок, CHECK из семи статусов), индекс активных позиций, `ADD COLUMN IF NOT EXISTS` для трейлинг- и execution-fact колонок, backfill `current_stop_price`, `DROP/ADD CONSTRAINT` для CHECK статуса, затем создание и сидирование `app_settings`.
- Блокирующие ошибки: отсутствие `trading.live_positions` или `trading.app_settings`, недостающие колонки, отсутствующий/узкий CHECK статуса, сбой чтения каталога.
- Только предупреждения: отсутствие ключей `app_settings` (исполнитель и так использует безопасные значения по умолчанию) и колонки вне контракта.
- Первые три оператора DDL сохраняют исторические позиции, потому что `test_runtime_migration_is_idempotent` проверяет их по индексам.

### Команды проверки

```powershell
# 23 новых + 68 существующих тестов исполнителя
cd f:\GIT\trading-terminal\backend; python -m pytest tests/test_live_schema.py tests/test_live_executor.py -q

# Read-only проверка контракта на реальной БД
cd f:\GIT\trading-terminal\backend; python ..\reports\173-issue-173-live-schema-preflight\verify_schema_contract.py
```

Результат на прод-БД (2026-09-18): `ok: true`, `columns_found: 30/30`, разрешены все семь статусов, оба ключа `app_settings` на месте, предупреждений нет.

### Известные ограничения

- `live_executor_preflight.py` по-прежнему требует Linux `/proc` и живой backend на `localhost:8000`; на Windows юнит-тестируется только его часть про схему.
- Два устаревших ассерта (появились до #173) всё ещё ожидают группу `closed` из двух статусов, как до #149: `test_live_trading_api.py::test_live_filters_target_closed_positions_ticker_and_dates` и `test_paper_trading_monitoring_api.py::test_monitoring_filters_support_closed_group_ticker_and_dates`. Зафиксировано в Issue #179, вне объёма #173.
