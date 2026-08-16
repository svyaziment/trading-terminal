# Контекст проекта: Trading Terminal

Последнее обновление: 2026-08-17 (задачи #59-#60 Live Trading Infrastructure; синхронизировано с английской версией). Источник: docs/refresh/context_collector.py + git ls-files.
Этот файл — канонический контекст проекта для агентов. Держите его актуальным.

## 1. Обзор проекта

Торговый терминал по акциям MOEX. Песочница (без реальной торговли). Стек: FastAPI backend (Python 3.12), React frontend (Vite + Tailwind + lightweight-charts), PostgreSQL (external, через host.docker.internal из Docker). Рыночные данные: T-Bank Invest API (gRPC, sandbox) + MOEX ISS API (REST, 1min свечи).

Три опоры:
1. **Backtest / Strategy Lab** - параметризуемый движок стратегий (AND-паттерны, multi-window confirmation, комиссия/слиппедж/RR, depth presets, bootstrap) + walk-forward validation, доступен через API и UI-конструктор.
2. **Paper trading** - активная стратегия из Strategy Lab (таблица `strategies`, `in_paper_test=true AND locked=true`) торгует виртуально через единый `StrategyEvaluator` (общий мозг с бэктестом). Текущая: `test_20260731` (levels_reversal + signal_4h_buy, RR 1:2, confirm 10min, 28 тикеров). Один arm: market вход, window mode (7-19 MSK), RR из конфига.

**Strategy Plugin System (Эпик #39):** стратегии подключаемы через `StrategyPlugin` ABC в `strategies/`. Зарегистрированные плагины: `levels_reversal` (обёртка над `StrategyEvaluator`), `atr_reversal` (ATR reversal Звездина). `portfolio_simulator.py` предоставляет backtest общего капитала (50k RUB, 10k слоты, макс 5 позиций, приоритет слотов по объёму, GAME OVER при cash<=0).
3. **Frontend dashboards** - Signals, Strategy Lab (backtest constructor), Paper Trading (A/B monitoring с фильтрами факторов + PnL chart).

## 2. Структура файлов
trading-terminal/
├── backend/
│ ├── app/
│ │ ├── api/
│ │ │ ├── market_data.py # T-Bank API: свечи, инструменты, top stocks
│ │ │ ├── data_refresh.py # POST /api/data/refresh (background, shared lock)
│ │ │ ├── signals_jobs.py # POST /api/signals/regenerate (background, shared lock)
│ │ │ ├── jobs_state.py # Общий in-process lock (refresh/regenerate/backtest/strategy)
│ │ │ ├── backtest_jobs.py # POST /api/backtest/run (legacy pattern matrix)
│ │ │ ├── levels_backtest_jobs.py # Эндпоинты матрицы levels backtest
│ │ │ ├── strategy_jobs.py # Хранение стратегий + backtest API (Strategy Lab)
│ │ │ ├── paper_trading_jobs.py # API мониторинга paper trading (overview/positions/dynamics)
│ │ │ ├── moex_1min_loader.py # Загрузчик 1min свечей MOEX ISS API (инкрементальный)
│ │ │ └── signals.py # GET /api/signals (legacy)
│ │ ├── analytics/
│ │ │ ├── indicators_manager.py # 33 технических индикатора
│ │ │ ├── signal_generator.py # Генерация сигналов из паттернов + индикаторов
│ │ │ ├── signal_engine.py # Применение паттернов к DataFrame индикаторов
│ │ │ ├── candles_aggregator.py # 30min raw -> candles_aggregated (1h,4h,1d,1w,1M)
│ │ │ ├── candles_1min_aggregator.py# 1min raw -> candles_aggregated (30min,1h,4h,1d), инкрементально
│ │ │ ├── data_refresher.py # Background: MOEX 1min + агрегация + индикаторы + сигналы
│ │ │ ├── backtest_engine.py # Детерминированный backtest engine (legacy pattern matrix)
│ │ │ ├── backtest_models.py # Контракт backtest (BacktestParams, ExitRule)
│ │ │ ├── levels_engine.py # 4h support/resistance levels + zones
│ │ │ ├── levels_backtest.py # Levels backtest (entry modes, confirmation, RR)
│ │ │ ├── levels_backtest_db.py # Сохранение levels backtest
│ │ │ ├── levels_refresher.py # Обновление levels
│ │ │ ├── strategy_backtest.py # Параметризуемый движок стратегий + walk-forward (Strategy Lab)
│   │   ├── strategy_context.py      # Построение контекста стратегии (уровни, ATR, BUY-сигналы)
│ │ │ ├── trading_config.py # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: торговая вселенная + реестр стратегий
│ │ │ ├── online_data.py # Стриминг: 1min свечи + стакан -> online_* таблицы
│ │ │ ├── orderbook_imbalance.py # Отношение bid/ask depth + обязательный live-фильтр
│ │ │ ├── online_signals.py # Движок онлайн-сигналов (paper trading, A/B arms)
│   │   ├── pattern_registry.py      # Реестр паттернов + normalize_patterns (Эпик #11)
│ │ │ ├── paper_trader.py # Движок paper trading (market+limit, stop/take, equity)
│   │   ├── paper_strategy.py        # Читатель активной paper-стратегии (из trading.strategies)
│   │   ├── strategies/            # StrategyPlugin архитектура (Эпик #39)
│   │   │   ├── base.py            # StrategyPlugin ABC + EntrySignal/ExitSignal/Position
│   │   │   ├── context.py         # MarketContext dataclass
│   │   │   ├── registry.py        # StrategyRegistry + register_default_strategies
│   │   │   ├── levels_reversal.py # LevelsReversalStrategy (обёртка над StrategyEvaluator)
│   │   │   └── atr_reversal.py    # ATR reversal стратегия (Звездин)
│   │   ├── portfolio_backtest.py  # Strategy-agnostic backtest через StrategyPlugin
│   │   ├── portfolio_simulator.py # Симулятор портфеля общего капитала (50k/10k слоты, GAME OVER)
│   │   ├── atr_backtest.py        # Фреймворк backtest ATR-стратегии
│ │ │ ├── position_catchup.py # Стартовый catch-up pending/open позиций
│ │ │ ├── top_stocks.py # Логика top stocks по объёму
│ │ │ └── patterns/ # 10 паттернов: trend/, mean_reversion/, breakout/, volume/, price_action/
│ │ ├── core/config_manager.py # Настройки (pydantic), logger, env vars
│ │ ├── broker/
│ │ │ ├── data_loader.py # Исторические свечи через T-Bank Invest API
│ │ │ └── tinkoff_sandbox.py # Только sandbox: ордера, баланс, позиции, отмена
│ │ ├── db/db_manager.py # Синхронный PostgreSQL manager (pool, select, execute, insert_with_schema)
│ │ └── main.py # FastAPI app, регистрация маршрутов
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ └── tests/
│       ├── test_strategy_plugin.py    # Бит-в-бит регрессионный тест (levels_reversal)
│       └── test_portfolio_simulator.py # Unit + integration тесты portfolio simulator
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Главное приложение (табы: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Таблица сигналов (сортировка, фильтр, пагинация)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: конструктор бэктестов + результаты + lock UI
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (фильтры, PnL chart, позиции)
│   │   ├── PatternSettingsModal.tsx # Schema-driven модалка настроек паттернов (Эпик #11)
│ │ │ ├── PipelineWidget.tsx # Виджет статуса refresh/regenerate
│ │ │ ├── CandleChart.tsx # Свечной график
│ │ │ ├── InstrumentsPanel.tsx # Список инструментов
│ │ │ ├── PatternStatsPanel.tsx # Статистика паттернов
│ │ │ ├── SignalDetailModal.tsx # Модальное окно сигнала
│ │ │ └── TopStocksPanel.tsx # Топ акций по объёму
│ │ ├── api.ts # API client
│ │ ├── types.ts # TypeScript types
│ │ └── index.css / main.tsx
│ └── package.json / tailwind.config.js / vite.config.js
├── analytics/ # Публикуемые аналитические результаты под контролем Git
│ └── issue-44-strategy-comparison/ # Notebook, отчёт, метрики и графики
├── docs/
│ ├── agents/ # project-context.md, handover.md (+ .ru versions), documentation-policy.md
│ ├── strategy/ # levels-reversal-strategy.md, paper-trading.md, testing-rules.md, backtest-report.md (+ .ru)
│ └── refresh/context_collector.py # Сборщик контекста для задач агента
├── scripts/ # Скрипты задач (gitignored) + сканеры refresh
├── start_processes.sh # Запуск процессов paper trading (catch-up + 4 процесса)
├── stop_processes.sh # Остановка процессов paper trading
└── docker-compose.yml # сервисы agent + backend (backend монтирует ./reports)

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
| **trading_universe** | 15 | Торговая вселенная (ticker, rank, pf, source) - top-15 по PF |
| **alerts** | ~72 | Онлайн-сигналы (details jsonb: price, support/take, factors) |
| backtest_runs | ~300 | Метаданные прогонов backtest (legacy + levels matrix) |
| backtest_trades | ~200k | Отдельные сделки (legacy matrix) |
| backtest_equity | ~200k | Кривая equity по прогонам (legacy matrix) |
| backtest_metrics | ~6.3k | Агрегированные метрики по прогонам/группам (PF, expectancy, win_rate, benchmarks) |

Ключевые колонки (новые таблицы):
- `strategies`: id, name (unique), config (jsonb: patterns, confirm_windows, commission_pct, slippage_pct, risk_reward, n_runs), in_paper_test (bool), locked (bool), description
- `backtest_results`: id, strategy_id (FK), ticker, test_type (full_sample/walkforward), depth, metrics (jsonb), created_at
- `paper_positions`: id, ticker, entry_ts/price, stop_price, take_price, limit_price, limit_ts, size_lots, size_rub, lot_size, status (pending/open/closed_stop/closed_take/cancelled), signal_source, window_mode, rr_mode, rr_ratio, entry_mode (market/limit), signal_id, strategy_name, exit_ts/price/reason, pnl_rub, pnl_pct
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 4. Конвейер данных

**Historical / refresh** (`data_refresher.py`, background, каждые 15 мин, вселенная top-15):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Поддерживает актуальность 4h BUY-сигналов для рукава base_4hbuy.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: читает активную стратегию из БД (`paper_strategy.get_active_paper_strategy`), строит 4h контекст через `build_strategy_context`, передаёт живые 1min бары в per-ticker `StrategyEvaluator` (единая логика входа, та же что в бэктесте), генерирует сигналы в `trading.alerts`.
- paper_trader: читает конфиг стратегии из БД (RR из `config.risk_reward`), alerts -> market positions (open по best_ask, один arm) -> мониторинг stop/take -> запись equity. Записывает `strategy_name` в `paper_positions`.
- При старте `start_processes.sh` запускает `position_catchup.py` (разбор pending + проверка open по историческим 1min свечам).

**Strategy Lab** (`strategy_backtest.py` через `strategy_jobs.py`): config -> backtest по тикерам (full-sample) + walk-forward (полугодия 2024-H2..2026-H1) -> backtest_results.

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
| POST | /api/strategies | Сохранить стратегию (отклоняет перезапись locked) |
| GET | /api/strategies | Список стратегий (with in_paper_test/locked/description) |
| GET | /api/strategies/run/status | Статус задачи backtest стратегии |
| GET | /api/strategies/data-range | Мин./макс. дата candles_1min_raw (для date pickers) |
| POST | /api/strategies/{id}/run | Запустить backtest (full_sample/walkforward, depth или кастомные date_from/date_to) |
| GET | /api/strategies/{id}/results | Результаты backtest (метрики по тикерам) |
| GET | /api/tickers/big | Тикеры с >= N 1min свечей (выбираемая вселенная) |
| GET | /api/paper-trading/overview | Имя стратегии + опции факторов + сводная статистика (фильтры факторов) |
| GET | /api/paper-trading/positions | Список позиций (фильтры + пагинация + сортировка) |
| GET | /api/paper-trading/dynamics | Кумулятивный ряд реализованного PnL с шагом 1h/1d/1w (фильтры факторов) |

Общий lock: jobs_state.py (in-process). Одновременно выполняется только одна тяжёлая задача; остальные возвращают 409.

## 6. Паттерны (10 всего)

| Категория | Паттерн | Описание |
|---|---|---|
| Тренд | Trend_SMA_Alignment | Выравнивание SMA (20/50/200) |
| Возврат к среднему | MR_RSI_Reversal | Разворот RSI из перекупленности/перепроданности |
| Пробой | BO_BB_Squeeze | Сжатие Bollinger Bands |
| Объём | VOL_Spike | Всплеск объёма (>2x от среднего) |
| Объём | VOL_Low_Pullback | Откат на низком объёме |
| Ценовое действие | PA_Hammer | Молот (бычий разворот) |
| Ценовое действие | PA_HangingMan | Повешенный (медвежий разворот) |
| Ценовое действие | PA_Engulfing | Поглощение (бычье/медвежье) |
| Ценовое действие | PA_ThreeWhiteSoldiers | Три белых солдата (бычий) |
| Ценовое действие | PA_ThreeBlackCrows | Три чёрные вороны (медвежий) |

Паттерны Strategy Lab (config-driven, логика AND): `levels_reversal` (4h support zone + confirmation), `signal_4h_buy` (active 4h BUY), `rsi_oversold`, `macd_bullish`, `bb_lower`. `levels_reversal` обязателен (определяет stop/take); остальные — AND-фильтры.

## 7. Известные проблемы и статус

- **Активная paper-стратегия**: `test_20260731` (id=36 в `trading.strategies`, `in_paper_test=true`, `locked=true`). Конфиг: levels_reversal (4h, swing+impulse, window 10, body 0.7, impulse 1.5, zone 0.5) + signal_4h_buy, confirm [10], RR 1:2, комиссия 0.06%. Вселенная: 28 тикеров из run_params. Верифицирована: 72 сигнала сгенерировано, 62 позиции открыто, первая закрытая сделка PnL +0.77%. Предыдущая валидированная стратегия `levels_reversal_4hbuy` остаётся в trading_config.py как reference.
- **Legacy pattern-matrix backtest**: rule-based стратегии НЕ прибыльны после комиссии на MOEX top-3 за 2 года (все PF < 1). Заменены подходом levels.
- **Вселенная**: top-15 по PF (trading_universe), единый источник истины через trading_config.get_trading_universe(). Все фоновые модули используют её.
- **Таймзона сессии**: timestamp свечей naive (в торговой логике считается MSK). session_only принудительно False в backtest v1.
- **Комиссия**: 0.06% round-trip (0.03% на сторону). Биржевой сбор не включён отдельно.
- **Индикаторы 1d**: нужно >=200 свечей; у некоторых тикеров меньше (warning, пропускаются).

## 8. Статус roadmap

| Блок | Описание | Статус |
|---|---|---|
| A | Базовая инфраструктура (FastAPI, DB, T-Bank API) | Готово |
| B | Индикаторы (33) | Готово |
| C | Паттерны (10) + сигналы | Готово |
| D | Frontend (SignalsPanel, PipelineWidget) | Готово |
| E | Background jobs (refresh, regenerate, shared lock) | Готово |
| F | Документация (project-context, handover, policy) | Готово (этот refresh) |
| G | Backtest engine + pattern matrix | Готово (legacy) |
| H | 1min свечи (MOEX ISS) + агрегация | Готово |
| K | Levels engine + levels backtest + matrix | Готово |
| L | Strategy Lab (параметризуемый движок + walk-forward + хранение + UI) | Готово |
| M | Paper trading (параметризованная стратегия из Strategy Lab, один arm market) | Готово (верифицировано: 72 сигнала, 62 позиции) |
| N | Trading universe (top-15 по PF, единый источник истины) | Готово |
| I | ML (CatBoost/LightGBM) | Не начато |
| J | Отчёт анализа A/B теста (signal_source x window x rr x entry) | Ожидает (накопить закрытые сделки) |
| O | Strategy Plugin System (StrategyPlugin ABC + registry + portfolio simulator) | Готово (Эпик #39) |
| P | Live Trading Infrastructure (sandbox-исполнение, рыночные фильтры, риск-контроль, панель управления) | В работе: sandbox broker client (#59) и realtime imbalance стакана (#60) готовы |

## 9. Важные замечания

- **Песочница**: реальной торговли нет. Используются sandbox-токены T-Bank API.
- **Секреты**: .env (`TINVEST_TOKEN` / `TINVEST_ACC` для рыночных данных, `TINVEST_SANDBOX` / необязательный `TINVEST_SANDBOX_ACC` для sandbox-исполнения, `PSTGRS_PWD`). Никогда не логировать секреты и не использовать реквизиты рыночных данных для торговли.
- **Docker**: после изменений кода пересоберите backend image (`docker compose up -d --build backend`). Backend монтирует `./reports` (для last_run.json).
- **Единый источник истины**: торговая вселенная + определения стратегий живут в `trading_config.py` / `trading.trading_universe`. Не хардкодьте списки тикеров или параметры стратегий в модулях.
- **Заблокированная стратегия**: стратегия в paper test имеет `locked=true`; API отклоняет её перезапись (409). Разблокировать только после тестового периода.
- **Логирование**: DBManager по умолчанию пишет в stdout. В скриптах, которые парсят JSON из stdout, перенаправьте логи в stderr. Фоновые процессы (start_processes.sh) используют `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` для немедленной записи логов в файлы.

## 10. Интеграция T-Bank Sandbox API

`backend/app/broker/tinkoff_sandbox.py` — граница исполнения ордеров для эпика #58. `TinkoffSandboxClient` подключается к отдельному endpoint `INVEST_GRPC_API_SANDBOX`, использует только `client.sandbox` и никогда не обращается к production-сервису `orders`. Клиент предоставляет:
- `execute_order` для market- и limit-ордеров (количество задаётся в лотах);
- `check_balance` для проверки свободных денег по валюте;
- `get_positions` для получения ненулевых открытых позиций портфеля;
- `cancel_order` для отмены активного sandbox-ордера.

Операционная политика централизована в `analytics/trading_config.py` (`SANDBOX_TRADING`): включение sandbox, жёсткий запрет реальной торговли, справочный начальный капитал, валюта по умолчанию, число retry/backoff и обнаружение счёта. Секреты там не хранятся: отдельные реквизиты `TINVEST_SANDBOX` / `TINVEST_SANDBOX_ACC` загружаются через `core/config_manager.py`; `TINVEST_TOKEN` / `TINVEST_ACC` используются только для рыночных данных.

При временных gRPC-ошибках (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `INTERNAL`) используется экспоненциальная задержка. Повтор ордера сохраняет один idempotency `order_id`, поэтому неопределённый ответ не приводит к дублирующему ордеру. Если `TINVEST_SANDBOX_ACC` пуст или некорректен (`50004`), клиент выбирает первый открытый sandbox-счёт и кеширует его id; счета и деньги автоматически не создаются.

Live-проверка 2026-08-16: оператор открыл sandbox-счёт и пополнил его на 50 000 RUB. `TinkoffSandboxClient` успешно прочитал баланс и позиции, выставил limit-ордер SBER на один лот и отменил его.

## 11. Imbalance стакана в реальном времени

`backend/app/analytics/orderbook_imbalance.py` — общий калькулятор и обязательный фильтр live-входа для задачи #60. При каждом streaming-обновлении стакана `online_data.py` суммирует объёмы первых 10 уровней bid и ask из конфига и сохраняет:

`volume_imbalance = bid_depth / ask_depth`

Инфраструктурные значения находятся в `trading_config.py` (`ORDERBOOK_IMBALANCE`): глубина 10, максимальный возраст агрегата 5 минут и порог по умолчанию 1.0. Активная стратегия может переопределить только верхнеуровневый `imbalance_threshold`; live-вход проходит, когда конечное значение imbalance строго больше порога.

Перед генерацией каждого сигнала `live_engine.py` заново рассчитывает отношение из `bid_depth` и `ask_depth` последней свежей строки `trading.online_orderbook_aggregates`. Отсутствующая или устаревшая строка, null/нечисловые значения и нулевая ask depth дают `None`, поэтому обязательный фильтр отклоняет сигнал, а не использует ноль или старые данные. Legacy online signal path использует тот же калькулятор.
