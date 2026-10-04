# Структура файлов

> **Source:** project-context.ru.md sections 2
> **Last refreshed:** 2026-10-04, task-346

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
│ │ │ ├── paper_trading_jobs.py # API paper-мониторинга (цены, фильтры, positions/dynamics)
│ │ │ ├── notifications.py # Кешированный статус подключения Telegram Bot API
│ │ │ ├── live_trading_jobs.py # API sandbox live-позиций и динамики PnL
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
│ │ │ ├── levels_engine.py # 4h S/R уровни + зоны; overlapping_resistance_zone_at veto (#97); LevelsTracker (#106); is_broken veto skip (#107)
│ │ │ ├── levels_backtest.py # Levels backtest (entry modes, confirmation, RR)
│ │ │ ├── levels_backtest_db.py # Сохранение levels backtest
│ │ │ ├── levels_refresher.py # Обновление levels
│ │ │ ├── strategy_backtest.py # Параметризуемый движок стратегий + walk-forward (Strategy Lab)
│   │   ├── strategy_context.py      # Построение контекста стратегии (уровни, ATR, BUY-сигналы, htf_bars)
│ │ │ ├── trading_config.py # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: вселенная, LIVE_UNIVERSE (12 имён PO; top-5 из #66 — история), стратегии, live risk policy, LEVEL_STATE_MACHINE (#106), LEVEL_BREAKOUT_RETEST (#107); трекер также для levels_sr_breakout (#117) и levels_sr_support (#127)
│ │ │ ├── position_sizer.py # Гибридный sizing по риску/концентрации + округление лотов
│ │ │ ├── live_executor.py # Sandbox-исполнение, защита, сверка позиций, shutdown
│ │ │ ├── live_schema.py # Контракт схемы live_positions (30 колонок / 7 статусов) + fail-fast проверка дрейфа (#173)
│ │ │ ├── moex_session.py # Календарь MOEX 10:00-19:00 МСК для overnight LiveExecutor (#137)
│ │ │ ├── moex_session.py # Календарь MOEX 10:00–19:00 МСК для overnight LiveExecutor (#137)
│ │ │ ├── live_executor_preflight.py # Read-only проверки перед sandbox canary
│ │ │ ├── online_data.py # Стриминг: 1min свечи + стакан -> online_* таблицы
│ │ │ ├── orderbook_imbalance.py # Отношение bid/ask depth + обязательный live-фильтр
│ │ │ ├── online_signals.py # Движок онлайн-сигналов (paper trading, A/B arms)
│   │   ├── pattern_registry.py      # Реестр паттернов + normalize_patterns (Эпик #11); схемы SignalEngine + timeframe (#80)
│   │   ├── signal_pattern_filters.py # Inline SignalEngine AND-фильтры для StrategyEvaluator (задача #79)
│ │ │ ├── paper_trader.py # Движок paper trading (market+limit, stop/take, equity)
│   │   ├── paper_strategy.py        # Читатель активной paper-стратегии (из trading.strategies)
│   │   ├── strategies/            # StrategyPlugin архитектура (Эпик #39)
│   │   │   ├── base.py            # StrategyPlugin ABC + EntrySignal/ExitSignal/Position
│   │   │   ├── context.py         # MarketContext dataclass (htf_bars для LevelsTracker, #116)
│   │   │   ├── registry.py        # StrategyRegistry + register_default_strategies
│   │   │   ├── levels_reversal.py # LevelsReversalStrategy (обёртка над StrategyEvaluator)
│   │   │   └── atr_reversal.py    # ATR reversal стратегия (Звездин)
│   │   ├── portfolio_backtest.py  # Strategy-agnostic backtest через StrategyPlugin
│   │   ├── portfolio_simulator.py # Симулятор портфеля общего капитала (50k/10k слоты, GAME OVER)
│   │   ├── atr_backtest.py        # Фреймворк backtest ATR-стратегии
│ │ │ ├── position_catchup.py # Стартовый catch-up pending/open позиций
│ │ │ ├── top_stocks.py # Логика top stocks по объёму
│ │ │ └── patterns/ # 10 модулей SignalEngine + Lab level_breakout_retest.py (#107) + levels_sr_breakout.py (#117) + levels_sr_support.py (#127; не под breakout/)
│ │ ├── core/config_manager.py # Настройки (pydantic), logger, env vars
│ │ ├── notifications/
│ │ │ └── telegram_notifier.py # Telegram Bot API alerts paper trading с rate limit
│ │ ├── broker/
│ │ │ ├── data_loader.py # Исторические свечи через T-Bank Invest API
│ │ │ └── tinkoff_sandbox.py # Только sandbox: ордера, баланс, позиции, отмена
│ │ ├── db/db_manager.py # Синхронный PostgreSQL manager (pool, select, execute, insert_with_schema)
│ │ └── main.py # FastAPI app, регистрация маршрутов
│ ├── Dockerfile # python:3.12-slim, T-Bank SDK, psycopg2
│ ├── migrations/ # Идемпотентные PostgreSQL-миграции, включая live_positions
│ └── tests/
│       ├── test_strategy_plugin.py    # Бит-в-бит регрессионный тест (levels_reversal)
│       ├── test_resistance_zone_veto.py # Задача #97 гард ALRS #711 по зоне сопротивления
│       ├── test_levels_state_machine.py # Задача #106 пробой / подтверждение / пропуск вето
│       ├── test_level_breakout_retest.py # Задача #107 ретест AND-фильтр / stop-take / пропуск вето
│       ├── test_levels_sr_breakout.py # Задача #117 композит OR-пути / source / гард движка
│       ├── test_levels_sr_support.py # Задача #127 только поддержка + вето с трекером / source / гард движка
│       ├── test_issue100_analysis.py # Задача #100 вселенная/вето/baseline Lab-прогона
│       ├── test_issue119_analysis.py # Задача #119 AFKS smoke конфиг/source/вердикт
│       ├── test_issue124_analysis.py # Задача #124 Lab-вселенная A/B / AFKS / ALRS / вердикт
│       ├── test_issue129_analysis.py # Задача #129 isolated support vs #124 B-support
│       ├── test_issue130_analysis.py # Задача #130 портфель 50k levels_sr_support vs #44/#103
│       └── test_portfolio_simulator.py # Unit + integration тесты portfolio simulator
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Главное приложение (табы: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Таблица сигналов (сортировка, фильтр, пагинация)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: чипы из GET /api/patterns (#82/#109/#118/#128)
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (фильтры, PnL chart, позиции)
│ │ │ ├── LiveTradingPanel.tsx # Live monitoring: открытые/история/equity/Telegram
│   │   ├── PatternSettingsModal.tsx # Schema-driven модалка настроек паттернов (Эпик #11; ошибки min/max #109)
│   │   ├── PatternIcon.tsx          # Иконки чипов Lab из API `icon` (breakout_up, support_breakout, support_tracker)
│ │ │ ├── PipelineWidget.tsx # Виджет статуса refresh/regenerate
│ │ │ ├── CandleChart.tsx # Свечной график
│ │ │ ├── InstrumentsPanel.tsx # Список инструментов
│ │ │ ├── PatternStatsPanel.tsx # Статистика паттернов
│ │ │ ├── SignalDetailModal.tsx # Модальное окно сигнала
│ │ │ └── TopStocksPanel.tsx # Топ акций по объёму
│ │ ├── api.ts # API client
│ │ ├── types.ts # TypeScript-типы (включая LevelBreakoutRetestConfig, LevelsSrBreakoutConfig, LevelsSrSupportConfig)
│ │ ├── patternLab.ts # Группировка чипов + RU/EN подписи + приоритет confirm_windows (#82/#109/#128)
│ │ ├── patternValidation.ts # Проверки min/max схемы перед save/run Lab (#109)
│ │ └── index.css / main.tsx
│ └── package.json / tailwind.config.js / vite.config.js
├── analytics/ # Публикуемые аналитические результаты под контролем Git
│ ├── issue-44-strategy-comparison/ # Notebook, отчёт, метрики и графики
│ ├── issue-66-live-universe/ # Рейтинг live top-5, отчёт и графики
│ ├── issue-100-test-20260820-portfolio/ # Портфельный replay Lab test_20260820 после вето #97
│ ├── issue-100-test-20260820-resistance-veto/ # Lab full-sample + walk-forward test_20260820 после вето #97
│ ├── issue-103-test-20260821-portfolio/ # Портфельный replay Lab test_20260821 после вето #97 (swing+impulse)
│ ├── issue-119-afks-sr-breakout-smoke/ # Изолированный AFKS A/B smoke для levels_sr_breakout (#119)
│ ├── issue-124-sr-breakout-universe/ # Изолированная Lab-вселенная A/B для levels_sr_breakout (#124)
│ ├── issue-129-sr-support-universe/ # Изолированная Lab-вселенная C vs #124 B-support (#129)
│ └── issue-130-sr-support-portfolio/ # Портфельный replay 50k levels_sr_support (#130)
├── docs/
│ ├── agents/ # project-context.md, handover.md (+ .ru versions), documentation-policy.md
│ ├── strategy/ # levels-reversal-strategy.md, paper-trading.md, live-trading.md, testing-rules.md, backtest-report.md (+ .ru)
│ └── refresh/context_collector.py # Сборщик контекста для задач агента
├── scripts/ # Скрипты задач (gitignored) + сканеры refresh
├── start_processes.sh # Запуск процессов paper trading (catch-up + 4 процесса)
├── stop_processes.sh # Остановка процессов paper trading
└── docker-compose.yml # сервисы agent + backend (backend монтирует ./reports)
