# Контекст проекта: Торговый терминал

> Последнее обновление: 2026-07-25 (task-070a). Источник: docs/refresh/current_tree.txt (git ls-files).
> Этот файл — канонический контекст проекта для агентов. Поддерживайте актуальность.

## 1. Обзор проекта

Торговый терминал для акций MOEX. Песочница (без реальной торговли). Стек: FastAPI backend (Python 3.12), React frontend (Vite), PostgreSQL (внешняя, через host.docker.internal из Docker). T-Bank Invest API для рыночных данных (gRPC, песочница). 10 паттернов технического анализа генерируют сигналы BUY/SELL. Детерминированный бэктест-движок валидирует стратегии на исторических данных.

## 2. Структура файлов
trading-terminal/
├── backend/
│ ├── app/
│ │ ├── api/
│ │ │ ├── market_data.py # T-Bank API: свечи, инструменты, топ акций
│ │ │ ├── data_refresh.py # POST /api/data/refresh (фон, общий лок)
│ │ │ ├── signals_jobs.py # POST /api/signals/regenerate (фон, общий лок)
│ │ │ ├── jobs_state.py # Общий in-process лок (refresh/regenerate/backtest)
│ │ │ ├── signals.py # GET /api/signals (legacy, может не использоваться)
│ │ │ ├── backtest_jobs.py # POST /api/backtest/run, GET /api/backtest/run/status (ещё не в git)
│ │ │ └── moex_1min_loader.py # MOEX ISS API загрузчик 1min свечей (инкрементальный, ещё не в git)
│ │ ├── analytics/
│ │ │ ├── indicators_manager.py # 33 технических индикатора (SMA, EMA, RSI, MACD, BB, ATR и т.д.)
│ │ │ ├── signal_generator.py # Генерация сигналов из паттернов + индикаторов
│ │ │ ├── signal_engine.py # Применение паттернов к DataFrame индикаторов
│ │ │ ├── candles_aggregator.py # 30min raw -> candles_aggregated (1h, 4h, 1d, 1w, 1M)
│ │ │ ├── candles_1min_aggregator.py # 1min raw -> candles_aggregated (30min, 1h, 4h, 1d), инкрементальный (ещё не в git)
│ │ │ ├── backtest_engine.py # Детерминированный бэктест-движок (ещё не в git)
│ │ │ ├── backtest_models.py # Контракт бэктеста (BacktestParams, ExitRule, константы) (ещё не в git)
│ │ │ ├── aggregate_candles.py # Legacy агрегация свечей (ранние задачи)
│ │ │ ├── pipeline.py # Legacy оркестрация пайплайна (ранние задачи)
│ │ │ ├── run_generate_signals.py # Legacy скрипт генерации сигналов (ранние задачи)
│ │ │ ├── top_stocks.py # Логика топ акций по объёму
│ │ │ └── patterns/ # 10 паттернов: trend/, mean_reversion/, breakout/, volume/, price_action/
│ │ ├── core/
│ │ │ ├── config_manager.py # Настройки (pydantic), логгер, переменные окружения
│ │ │ └── config.py # Legacy конфиг (ранние задачи)
│ │ ├── db/
│ │ │ ├── db_manager.py # Синхронный PostgreSQL менеджер (пул, select, execute, insert_with_schema)
│ │ │ ├── audit_raw.py # Утилита аудита raw данных
│ │ │ ├── check_db_connection.py # Утилита проверки соединения с БД
│ │ │ ├── check_external.py # Утилита проверки внешней связности
│ │ │ └── check_network.py # Утилита проверки сети
│ │ ├── broker/ # Legacy T-Bank broker загрузчик (ранние задачи, не используется в текущем пайплайне)
│ │ │ ├── check_loader.py
│ │ │ └── data_loader.py
│ │ └── main.py # FastAPI приложение, регистрация роутов
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ ├── requirements.txt / requirements-dev.txt
│ └── tests/
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Главное приложение (SignalsPanel, PipelineWidget)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Таблица сигналов (сортировка, фильтр, пагинация)
│ │ │ ├── PipelineWidget.tsx # Виджет статуса refresh/regenerate (поллит /api/jobs/status)
│ │ │ ├── CandleChart.tsx # Компонент графика свечей
│ │ │ ├── InstrumentsPanel.tsx # Панель списка инструментов
│ │ │ ├── PatternStatsPanel.tsx # Панель статистики паттернов
│ │ │ ├── RegenerateWidget.tsx # Виджет регенерации сигналов
│ │ │ ├── SignalDetailModal.tsx # Модальное окно деталей сигнала
│ │ │ └── TopStocksPanel.tsx # Панель топ акций по объёму
│ │ ├── api.ts # API клиент (legacy имя; client.ts в доках)
│ │ ├── types.ts # TypeScript типы
│ │ ├── index.css / main.tsx
│ │ └── tailwind.config.js / vite.config.js
│ └── package.json
├── docs/
│ ├── agents/
│ │ ├── project-context.md # Английская версия
│ │ ├── project-context.ru.md # Этот файл
│ │ ├── handover.md # Руководство передачи для агентов
│ │ ├── handover.ru.md # Русская версия
│ │ └── documentation-policy.md # Политика документации
│ └── refresh/ # Артефакты обновления (file_scan_report.md, db_schema.md, current_tree.txt) (ещё не в git)
├── scripts/ # Скрипты задач (в gitignore)
│ ├── refresh/refresh-project-docs.sh # Обёртка сканеров обновления
│ ├── targeted_project_scanner.py # Сканер файлов
│ └── db_schema_scanner.py # Сканер схемы БД
├── docker-compose.yml # Сервисы backend + frontend
└── .env # Секреты (TINVEST_TOKEN, PSTGRS_PWD и т.д.)

## 3. Схема БД (PostgreSQL, схема: trading)

| Таблица | Строк (прибл.) | Описание |
|---|---|---|
| candles_30min_raw | ~28k | 30min свечи из T-Bank API (30 тикеров, ~1 месяц) |
| candles_1min_raw | ~900k | 1min свечи из MOEX ISS API (SBER/GAZP/VTBR, 2 года) |
| candles_aggregated | ~100k | Агрегированные свечи (30min, 1h, 4h, 1d, 1w, 1M) |
| indicators | ~60k | 33 технических индикатора на свечу |
| signals | ~45k | Сигналы BUY/SELL (10 паттернов, confidence, total_signals) |
| instruments | ~4.3k | Метаданные тикеров (figi, lot_size, min_price_increment) |
| top_stocks_by_volume | 30 | Топ 30 тикеров по объёму |
| backtest_runs | ~100 | Метаданные прогонов бэктеста (params, status, total_trades) |
| backtest_trades | ~200k | Отдельные сделки (вход/выход, PnL, издержки) |
| backtest_equity | ~200k | Кривая эквити на прогон |
| backtest_metrics | ~3k | Агрегированные метрики на прогон/группу (PF, expectancy, win_rate, бенчмарки) |

Ключевые колонки:
- candles_1min_raw: ticker, figi, timestamp (PK: ticker, timestamp), open/high/low/close, volume
- backtest_runs: id, strategy_name, params (jsonb), universe_snapshot (jsonb), selection_bias, status, total_trades
- backtest_trades: run_id (FK), ticker, timeframe, signal_id, pattern_name, side (LONG), entry_ts/price, exit_ts/price, exit_reason, bars_held, gross/commission/slippage/net_return_pct, pnl_rub
- backtest_metrics: run_id (FK), group_key (ALL или pattern=X|tf=Y), n_trades, profit_factor, expectancy, win_rate, sharpe, sortino, max_drawdown, reliable, benchmark_buyhold/random_return_pct

## 4. Пайплайн данных

1. **Загрузка рыночных данных**: T-Bank Invest API (gRPC, песочница) -> candles_30min_raw (30 тикеров, 30min свечи).
2. **Загрузка 1min свечей**: MOEX ISS API (REST) -> candles_1min_raw (SBER/GAZP/VTBR, 1min свечи, 2 года). Инкрементальный загрузчик (backend/app/api/moex_1min_loader.py).
3. **Агрегация**: candles_30min_raw -> candles_aggregated (1h, 4h, 1d, 1w, 1M) через candles_aggregator.py. candles_1min_raw -> candles_aggregated (30min, 1h, 4h, 1d) через candles_1min_aggregator.py (инкрементальный).
4. **Индикаторы**: candles_aggregated -> indicators (33 индикатора на свечу) через indicators_manager.py.
5. **Сигналы**: indicators + patterns -> signals (BUY/SELL, confidence, total_signals, pattern_name) через signal_generator.py + signal_engine.py.
6. **Бэктест**: signals + candles_aggregated + indicators -> backtest_runs/trades/equity/metrics через backtest_engine.py + backtest_jobs.py.

## 5. API эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| GET | /health | Проверка здоровья |
| GET | /api/candles | Свечи (ticker, timeframe, limit) |
| GET | /api/instruments | Список инструментов |
| GET | /api/top-stocks | Топ 30 по объёму |
| GET | /api/signals | Сигналы (ticker, timeframe, limit) |
| POST | /api/data/refresh | Фон: загрузка свечей + агрегация + индикаторы + сигналы (общий лок) |
| POST | /api/signals/regenerate | Фон: регенерация сигналов (общий лок) |
| GET | /api/jobs/status | Статус всех джобов (refresh, regenerate, backtest) |
| POST | /api/backtest/run | Фон: запуск бэктест-матрицы (общий лок). Параметры: quick, universe_limit, signal_exit, tickers |
| GET | /api/backtest/run/status | Статус бэктест-джоба (прогресс, combo_results) |

Общий лок: jobs_state.py (in-process). Только один тяжёлый джоб (refresh/regenerate/backtest) выполняется одновременно. Остальные возвращают 409.

## 6. Паттерны (10 всего)

| Категория | Паттерн | Описание |
|---|---|---|
| Trend | Trend_SMA_Alignment | Выравнивание SMA (20/50/200) |
| Mean Reversion | MR_RSI_Reversal | Разворот RSI из перекупленности/перепроданности |
| Breakout | BO_BB_Squeeze | Сжатие полос Боллинджера |
| Volume | VOL_Spike | Всплеск объёма (>2x среднего) |
| Volume | VOL_Low_Pullback | Откат на низком объёме |
| Price Action | PA_Hammer | Молот (бычий разворот) |
| Price Action | PA_HangingMan | Повешенный (медвежий разворот) |
| Price Action | PA_Engulfing | Поглощение (бычье/медвежье) |
| Price Action | PA_ThreeWhiteSoldiers | Три белых солдата (бычий) |
| Price Action | PA_ThreeBlackCrows | Три чёрные вороны (медвежий) |

## 7. Известные проблемы и статус

- **1d сигналы**: Теперь присутствуют (213-215 на тикер на 2-летней истории). Ранее 0 из-за недостатка 1d индикаторов (274 строки). Исправлено загрузкой 2-летней 1min истории и агрегацией в 1d.
- **Результаты бэктеста (2-летняя история, SBER/GAZP/VTBR)**: Rule-based стратегии НЕ прибыльны после комиссии (0.06% round-trip). Все 30 комбинаций матрицы имеют PF < 1, expectancy < 0. Лучшая: filter_ts2_c0.9_sigOn (PF 0.800, exp -0.066%). Стратегия уступает buy&hold и random на сильных сигналах. По тикерам: GAZP лучший PF (0.870), SBER худший (0.581), VTBR buy&hold сильно положителен (+0.17%), но стратегия теряет (-0.08%). Выход по любому сигналу (sigOn) лучше выхода по сильному сигналу (sigOn_ts3) или без выхода по сигналу (sigOff).
- **Таймзона сессии**: Таймзона timestamp в candles_aggregated не верифицирована (вероятно UTC). session_only выход принудительно выключен в бэктесте v1.
- **Смещение вселенной**: Бэктест использует фиксированный топ-30 (selection_bias=true). Скользящая вселенная не реализована.
- **Комиссия**: 0.06% round-trip (0.03% на сторону). Биржевая комиссия не включена отдельно.
- **Незакоммиченные файлы**: backtest_jobs.py, moex_1min_loader.py, candles_1min_aggregator.py, backtest_engine.py, backtest_models.py, docs/refresh/ ещё не в git (изменения task-049..069 ожидают коммита).

## 8. Статус roadmap

| Блок | Описание | Статус |
|---|---|---|
| A | Базовая инфраструктура (FastAPI, БД, T-Bank API) | Готово |
| B | Индикаторы (33) | Готово |
| C | Паттерны (10) + сигналы | Готово |
| D | Frontend (SignalsPanel, PipelineWidget) | Готово |
| E | Фоновые джобы (refresh, regenerate, общий лок) | Готово |
| F | Документация (project-context, handover, policy) | Готово (это обновление) |
| G | Бэктест-движок + матрица | Готово (task-049..065, ещё не в git) |
| H | 1min свечи (MOEX ISS API) + агрегация | Готово (task-054..063, ещё не в git) |
| I | ML (CatBoost/LightGBM) | Не начато |
| J | Frontend визуализация бэктеста | Не начато |

## 9. Важные заметки

- **Песочница**: Без реальной торговли. T-Bank API песочница токены.
- **Секреты**: .env (TINVEST_TOKEN, PSTGRS_PWD). Никогда не логируйте секреты.
- **Docker**: Образ backend нужно пересобирать после изменений кода (docker compose up -d --build backend).
- **MSYS конвертация путей**: Используйте MSYS_NO_PATHCONV=1 для docker команд с абсолютными путями в Git Bash.
- **Логирование**: DBManager логирует в stdout по умолчанию. Перенаправляйте в stderr в скриптах, чтобы stdout был чистым для JSON.
- **Вывод бэктеста**: Rule-based стратегии (паттерны -> сигналы -> стоп/тейк/holding) НЕ имеют edge после комиссии на MOEX топ-3 (SBER/GAZP/VTBR) за 2 года. Сигналы несут слабую directional информацию (buy&hold положителен на сильных сигналах), но правила выхода и комиссия убивают её. Следующие шаги: ML на индикаторах (не паттернах), или пересмотр гипотезы, или принять результат.
- **История данных**: 1min свечи загружены для SBER/GAZP/VTBR (2 года). Остальные 27 тикеров всё ещё имеют ~1 месяц (из T-Bank API 30min raw). Расширяйте через moex_1min_loader.py для полной вселенной.
- **Legacy файлы**: broker/, aggregate_candles.py, pipeline.py, run_generate_signals.py, config.py, signals.py из ранних задач (task-000..031) и могут не использоваться в текущем пайплайне. Оставьте для справки; не удаляйте без верификации.
