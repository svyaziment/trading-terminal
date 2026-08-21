# Контекст проекта: Trading Terminal

Последнее обновление: 2026-08-21 (задача #107 level_breakout_retest). Источник: docs/refresh/context_collector.py + git ls-files.
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
│ │ │ ├── trading_config.py # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ: вселенная, live top-5, стратегии, live risk policy, LEVEL_STATE_MACHINE (#106), LEVEL_BREAKOUT_RETEST (#107)
│ │ │ ├── position_sizer.py # Гибридный sizing по риску/концентрации + округление лотов
│ │ │ ├── live_executor.py # Sandbox-исполнение, защита, сверка позиций, shutdown
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
│   │   │   ├── context.py         # MarketContext dataclass
│   │   │   ├── registry.py        # StrategyRegistry + register_default_strategies
│   │   │   ├── levels_reversal.py # LevelsReversalStrategy (обёртка над StrategyEvaluator)
│   │   │   └── atr_reversal.py    # ATR reversal стратегия (Звездин)
│   │   ├── portfolio_backtest.py  # Strategy-agnostic backtest через StrategyPlugin
│   │   ├── portfolio_simulator.py # Симулятор портфеля общего капитала (50k/10k слоты, GAME OVER)
│   │   ├── atr_backtest.py        # Фреймворк backtest ATR-стратегии
│ │ │ ├── position_catchup.py # Стартовый catch-up pending/open позиций
│ │ │ ├── top_stocks.py # Логика top stocks по объёму
│ │ │ └── patterns/ # 10 модулей SignalEngine + Lab level_breakout_retest.py (#107; не под breakout/)
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
│       ├── test_issue100_analysis.py # Задача #100 вселенная/вето/baseline Lab-прогона
│       └── test_portfolio_simulator.py # Unit + integration тесты portfolio simulator
├── frontend/
│ ├── src/
│ │ ├── App.tsx # Главное приложение (табы: Signals, Stats, Top-30, Instruments, Lab, Paper Trading)
│ │ ├── components/
│ │ │ ├── SignalsPanel.tsx # Таблица сигналов (сортировка, фильтр, пагинация)
│ │ │ ├── StrategyLab.tsx # Strategy Lab: конструктор бэктестов + результаты + lock UI
│ │ │ ├── PaperTradingPanel.tsx # Paper Trading: A/B dashboard (фильтры, PnL chart, позиции)
│ │ │ ├── LiveTradingPanel.tsx # Live monitoring: открытые/история/equity/Telegram
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
│ ├── issue-44-strategy-comparison/ # Notebook, отчёт, метрики и графики
│ ├── issue-66-live-universe/ # Рейтинг live top-5, отчёт и графики
│ ├── issue-100-test-20260820-portfolio/ # Портфельный replay Lab test_20260820 после вето #97
│ ├── issue-100-test-20260820-resistance-veto/ # Lab full-sample + walk-forward test_20260820 после вето #97
│ └── issue-103-test-20260821-portfolio/ # Портфельный replay Lab test_20260821 после вето #97 (swing+impulse)
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
| **live_positions** | runtime | Sandbox-ордера T-Bank, идентификаторы защиты, жизненный цикл и PnL |
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
- `live_positions`: id, ticker, instrument_id, signal_ts, entry_price, lot_size, size_lots, stop_price, take_price, broker_order_id/stop_id/take_id, status, strategy_name, exit_ts/price/reason, pnl_rub
- `paper_equity`: id, timestamp, equity_rub, realized_pnl, open_positions, drawdown_pct
- `trading_universe`: ticker (PK), rank, pf, source, notes, updated_at
- `alerts`: id, alert_type, ticker, message, details (jsonb), created_at

## 4. Конвейер данных

**Historical / refresh** (`data_refresher.py`, background, каждые 15 мин, вселенная top-15):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Поддерживает актуальность 4h BUY-сигналов для рукава base_4hbuy.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: читает активную стратегию из БД (`paper_strategy.get_active_paper_strategy`), строит 4h контекст через `build_strategy_context`, передаёт живые 1min бары в per-ticker `StrategyEvaluator` (единая логика входа, та же что в бэктесте), генерирует сигналы в `trading.alerts`.
- paper_trader: читает конфиг стратегии из БД (RR из `config.risk_reward`), alerts -> market positions (open по best_ask, один arm) -> мониторинг stop/take -> запись equity. Записывает `strategy_name` в `paper_positions` и в best-effort режиме отправляет Telegram alerts для открытий, закрытий, stop/take, пересечения порога drawdown и GAME OVER.
- При старте `start_processes.sh` запускает `position_catchup.py` (разбор pending + проверка open по историческим 1min свечам).

**Sandbox live execution** (`live_executor.py`, опциональный фоновый процесс): использует тот же `StrategyEvaluator` и live 1min контекст, затем требует свежий imbalance стакана, проверяет sandbox-баланс, рассчитывает целое число лотов, выставляет market BUY и записывает позицию в `trading.live_positions`. Запуск только явно: `START_LIVE_EXECUTOR=1 ./start_processes.sh`; обычный paper-запуск не выставляет брокерские ордера.

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
| GET | /api/patterns | Схемы реестра паттернов (Strategy Lab) |
| POST | /api/patterns/preview | Превью паттерна на графике: свечи + overlays (`ray`, `band`, `line`, `marker`); #88 — `levels_reversal` |
| POST | /api/strategies | Сохранить стратегию (отклоняет перезапись locked) |
| GET | /api/strategies | Список стратегий (with in_paper_test/locked/description) |
| GET | /api/strategies/run/status | Статус задачи backtest стратегии |
| GET | /api/strategies/data-range | Мин./макс. дата candles_1min_raw (для date pickers) |
| POST | /api/strategies/{id}/run | Запустить backtest (full_sample/walkforward, depth или кастомные date_from/date_to) |
| GET | /api/strategies/{id}/results | Результаты backtest (метрики по тикерам) |
| GET | /api/tickers/big | Тикеры с >= N 1min свечей (выбираемая вселенная) |
| GET | /api/paper-trading/overview | Имя стратегии + опции факторов + сводная статистика (фильтры факторов) |
| GET | /api/paper-trading/positions | Список позиций (фильтры + пагинация + сортировка); открытые строки содержат текущую цену и нереализованный PnL |
| GET | /api/paper-trading/dynamics | Кумулятивный ряд реализованного PnL с шагом 1h/1d/1w (фильтры факторов/тикера/дат) |
| GET | /api/notifications/status | Кешированный статус конфигурации и подключения Telegram Bot API |
| GET | /api/live-trading/positions | Sandbox live-позиции с текущей ценой, PnL, фильтрами, сортировкой и пагинацией |
| GET | /api/live-trading/dynamics | Кумулятивный sandbox PnL с шагом 1h/1d/1w |

Общий lock: jobs_state.py (in-process). Одновременно выполняется только одна тяжёлая задача; остальные возвращают 409.

## 6. Паттерны (10 всего)

Вкладка Signals по-прежнему пишет десять классов `BasePattern` ниже в `trading.signals` (confidence, BUY/SELL, total_signals). Эта таблица **не** является путём Lab-фильтра, кроме `signal_4h_buy`.

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

Паттерны Strategy Lab (config-driven, логика AND, один конфиг для бэктеста / paper / live):
- `levels_reversal` — обязателен; 4h зона поддержки + подтверждение; задаёт stop/take. Задача #97: `check_entry` отклоняет бар, если 1min close лежит в активной зоне сопротивления (`overlapping_resistance_zone_at`); это дефект, а не role-reversal. Задача #106: при колонке `state` вето пропускает зоны не в `active`. Задача #107: `StrategyEvaluator` передаёт `LevelsTracker` в вето только если включён `level_breakout_retest`; у `build_levels` колонки `state` нет, locked `test_20260731` остаётся бит-в-бит.
- `level_breakout_retest` — Lab AND-фильтр после `levels_reversal` (эпик #105 / задача #107). Подтверждённый пробой сопротивления + ретест в `[level ± retest_zone_atr×ATR]` + close ≥ пробитого уровня + бычий триггер. Stop/take = ATR × RR из параметров паттерна. Не SignalEngine inline-evaluate id (не добавлять в `SIGNAL_ENGINE_PATTERN_IDS`). Схема в `PATTERN_REGISTRY` и дублируется в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для `GET /api/patterns`. Файл: `patterns/level_breakout_retest.py` (не класть в `patterns/breakout/` — затенит `breakout.py` / `BO_BB_Squeeze`).
- `signal_4h_buy` — агрегат 4h BUY из `trading.signals` (ТФ фиксирован; не рефакторится).
- `rsi_oversold` / `macd_bullish` / `bb_lower` — 1min индикаторные AND-фильтры. `rsi_oversold` не является `MR_RSI_Reversal`.
- Десять id SignalEngine выше — AND-фильтры на последней закрытой HTF-свече через inline `BasePattern.evaluate` по `trading.indicators`. Схемы из `GET /api/patterns` (select `timeframe` 30min/1h/2h/4h/1d/1w, по умолчанию 4h, плюс числовые дефолты 4h). Таймфрейм задаётся в модалке настроек. `StrategyLab.tsx` группирует чипы по `category` из API (RU-заголовки: levels / signal / trend / price_action / volume / mean_reversion / breakout). Десять id SignalEngine не хардкодятся; fallback из двух чипов используется только если `GET /api/patterns` пуст.

## 7. Известные проблемы и статус

- **Активная paper-стратегия**: `test_20260731` (id=36 в `trading.strategies`, `in_paper_test=true`, `locked=true`). Конфиг: levels_reversal (4h, swing+impulse, window 10, body 0.7, impulse 1.5, zone 0.5) + signal_4h_buy, confirm [10], RR 1:2, комиссия 0.06%. Вселенная: 28 тикеров из run_params. Верифицирована: 72 сигнала сгенерировано, 62 позиции открыто, первая закрытая сделка PnL +0.77%. Предыдущая валидированная стратегия `levels_reversal_4hbuy` остаётся в trading_config.py как reference. Locked-строка БД задачей #97 не перезаписывается.
- **Задача #97 (ALRS paper #711, 2026-08-20)**: `levels_reversal` напечатал вход от поддержки по 19.80, пока цена сидела в импульсном сопротивлении 19.67 [19.40, 19.94]. `nearest_level_at(..., 'support')` односторонний; расширение зоны поддержки на 0.5×ATR пропустило fill. Гард: `overlapping_resistance_zone_at` ветирует `StrategyEvaluator.check_entry`. Разбор: `docs/strategy/levels-reversal-strategy.ru.md` §10. Тест: `tests/test_resistance_zone_veto.py`.
- **Задача #100 (`test_20260820`, 2026-08-21)**: разблокированный swing-only Lab-конфиг id=102 после вето #97. Два пакета, ни один не lock/paper-flag: (1) портфельный replay Issue #44 (`analytics/issue-100-test-20260820-portfolio/`) — equity 87 033.31 RUB, PF 1.37, 1721 сделка; (2) Lab full-sample + walk-forward на `get_big_tickers` (`analytics/issue-100-test-20260820-resistance-veto/`) — 28 тикеров, median PF 1.52, 26/28 PF>1, 2556 сделок, WF avg PF 1.91. Бар ALRS 2026-08-20 11:50:24 @ 19.80 отсутствует в обоих trade list. Не смешивать с locked `test_20260731`.
- **Задача #103 (`test_20260821`, 2026-08-21)**: разблокированный Lab-конфиг id=118 после вето #97, `level_method=['swing','impulse']` (как у locked `test_20260731`, текущий `StrategyEvaluator`). Пакет `analytics/issue-103-test-20260821-portfolio/` — equity 89 055.31 RUB, PF 1.34, 2070 сделок, daily Max DD 6.82%, без GAME OVER. Бар ALRS 2026-08-20 11:50:24 @ 19.80 отсутствует среди candidate и портфельных входов. Не lock/rename/overwrite `test_20260821`, `test_20260820` и locked `test_20260731`. Это не таблица Lab full-sample и не сравнение с ATR.
- **Задача #106 (эпик #105, 2026-08-21)**: in-memory `LevelsTracker` в `levels_engine.py` ведёт `active → broken_up/down → flipped_support/resistance`. Пороги пробоя — `LEVEL_STATE_MACHINE` в `trading_config.py`. `overlapping_resistance_zone_at` пропускает строки не в `active`, если есть колонка `state`. Без персистентности в БД. Тесты: `tests/test_levels_state_machine.py`.
- **Задача #107 (эпик #105, 2026-08-21)**: Lab-паттерн `level_breakout_retest` — AND-фильтр в `StrategyEvaluator` после `levels_reversal`. Трекер создаётся и передаётся в вето (`is_broken`) только если паттерн есть в `config.patterns`. Stop/take тогда из `stop_atr` × ATR и `risk_reward` паттерна. Locked `test_20260731` паттерн не включает, поэтому вето #97 и levels stop/take остаются бит-в-бит. Тесты: `tests/test_level_breakout_retest.py` плюс существующие `tests/test_resistance_zone_veto.py` / `tests/test_levels_state_machine.py`. Далее: аналитическая валидация (#3) и чип Lab (#4).
- **Legacy pattern-matrix backtest**: rule-based стратегии НЕ прибыльны после комиссии на MOEX top-3 за 2 года (все PF < 1). Заменены подходом levels.
- **Вселенная**: top-15 по PF (`trading_universe`) остаётся вселенной paper/data-refresh через `get_trading_universe()`. Sandbox live execution использует топ-5 из задачи #66 `LIVE_UNIVERSE` = SBER, LKOH, RUAL, NVTK, GAZP через `get_live_trading_universe()`. На снимке #66 таблица `paper_positions` была пуста (equity плоская 100 000 RUB), поэтому live-список построен по бэктесту, ликвидности и ATR, а не по forward PnL.
- **Sandbox canary (задача #74, 2026-08-19)**: `LiveExecutor` инициализировал топ-5 на locked-стратегии `test_20260731` и отправил sandbox market BUY по RUAL (37 лотов по 26.73, take 28.02, stop 26.19). Следующий сигнал по тому же тикеру был пропущен с `reason=duplicate_ticker`. `paper_equity` продолжала писаться во время сессии. Runbook — в handover §19.
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
| P | Live Trading Infrastructure (sandbox-исполнение, рыночные фильтры, риск-контроль, alerting, панель управления) | Backend-исполнение #59-#62, Telegram #64, monitoring panel #65, live-вселенная #66, логи отказов #73 и первая sandbox canary #74 готовы |
| Q | Паттерны SignalEngine в Strategy Lab (эпик #78) | #79–#82 готовы (evaluator, схемы registry, E2E/docs, группировка UI Lab) |
| R | Превью паттерна на графике Lab + Сигналы (эпик #87) | #88 API preview + оверлеи levels готовы; #89–#92 далее |
| S | Пробой уровня и смена роли (эпик #105) | #106 LevelsTracker + #107 `level_breakout_retest` AND-фильтр готовы; Lab UI / валидация далее |

## 9. Важные замечания

- **Песочница**: реальной торговли нет. Используются sandbox-токены T-Bank API.
- **Секреты**: .env (`TINVEST_TOKEN` / `TINVEST_ACC` для рыночных данных, `TINVEST_SANDBOX` / необязательный `TINVEST_SANDBOX_ACC` для sandbox-исполнения, `TGM_TOKEN` / `TGM_CHAT` для Telegram, `PSTGRS_PWD`). Никогда не логировать секреты и не использовать реквизиты рыночных данных для торговли.
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

## 12. Расчёт размера позиции

`backend/app/analytics/position_sizer.py` предоставляет общую функцию `calculate_position_size()` для расчёта live-ордера. Сначала она определяет бюджет позиции по заданному риску на сделку и расстоянию до stop-loss, затем ограничивает бюджет максимальной долей одной позиции в портфеле:

`size_rub = min(capital_rub * risk_per_trade_pct / stop_distance_pct, capital_rub * max_position_pct / 100)`

Исполняемое количество — целое число лотов, помещающихся в бюджет: `floor(size_rub / (price * lot_size))`. Если бюджет меньше одного лота, но свободного капитала достаточно для его оплаты, результат повышается до одного лота с причиной `min_lot`. Неположительное расстояние до stop-loss возвращает `invalid_stop`, а капитал меньше стоимости полного лота — `insufficient_capital`.

Лимиты по умолчанию централизованы в `trading_config.py` (`POSITION_SIZING`): риск 1% на сделку и не более 20% капитала в одной позиции. Результат также сообщает, что определило итоговый размер: риск, концентрация или правило минимального лота.

## 13. Sandbox live executor

`backend/app/analytics/live_executor.py` реализует `LiveExecutor` без изменений `StrategyEvaluator`. При инициализации он пересекает тикеры locked paper-стратегии с `get_live_trading_universe()` (задача #66: SBER, LKOH, RUAL, NVTK, GAZP), чтобы sandbox-ордера оставались на именах со свежим стаканом. Для каждого тикера он загружает активную заблокированную стратегию и 4h-контекст, передаёт последнюю закрытую строку из `online_candles_1min` в `check_entry` и до любого обращения к брокеру применяет обязательный фильтр свежего imbalance. Прошедший BUY проверяет свободные RUB, рассчитывает размер через `calculate_position_size`, отправляет sandbox market-ордер и сохраняет broker IDs и состояние в `trading.live_positions`. Каждый отказ BUY пишется одной структурированной строкой с тикером, стабильным кодом `reason` и релевантными числами; тишина в `executor.log` означает, что `StrategyEvaluator` не дал BUY, а не то, что фильтр мёртв. Read-only preflight — в `live_executor_preflight.py`; runbook первой сессии — handover §19.

Take-profit сразу выставляется как ожидающий sell-limit. Stop-loss намеренно реализован как синтетический триггер: sell-limit ниже текущего рынка исполнился бы сразу, поэтому executor ждёт, когда текущая цена брокера достигнет stop, отменяет take и только затем выставляет stop sell-limit по наблюдаемой цене. Сверка позиций опрашивает `get_positions()`; исчезновение позиции после take или сработавшего stop закрывает строку БД и рассчитывает PnL. Внешний SELL отменяет защитные заявки и закрывает позицию sandbox market-ордером.

Каждая попытка обращения к broker API, включая внутренние retry и обнаружение счёта, проходит через token bucket с потолком 10 запросов/сек. SIGTERM/SIGINT только устанавливает флаг остановки; финальная очистка отменяет ожидающие entry/protection заявки, обновляет состояние БД, при включённом `close_positions_on_shutdown` закрывает открытые sandbox-позиции и закрывает DB pool standalone-процесса. Полную политику возвращает `get_live_trading_config()` из `trading_config.py`.

## 14. Telegram alerting

`backend/app/notifications/telegram_notifier.py` отправляет Markdown-сообщения через Telegram Bot API. Используются `TGM_TOKEN` и `TGM_CHAT`; прежнее имя `TGM_CHAT_ID` сохранено как fallback. `TGM_APP_ID` и `TGM_APP_HASH` загружаются для совместимости конфигурации, но Bot API их не требует.

Отправка сериализована и ограничена одной попыткой в секунду. Сетевые ошибки и ошибки API логируются и возвращают `False`, но не попадают в торговый цикл. `paper_trader.py` отправляет alerts после успешной записи в БД для market/limit открытий и каждого закрытия, включая stop/take. При обновлении equity критический alert отправляется только при первом пересечении `risk.max_daily_loss_pct` или первом достижении нулевого equity (GAME OVER), поэтому каждый цикл не создаёт повторное сообщение.

## 15. Панель мониторинга Live Trading

`frontend/src/components/LiveTradingPanel.tsx` доступна во вкладке `Live Trading`. Панель каждые 10 секунд читает `trading.live_positions` через live monitoring API и показывает открытые позиции с последним best bid (fallback на best ask), нереализованный PnL в RUB/%, пагинируемую и сортируемую историю сделок, накопленный realized PnL и подключение Telegram. Обе таблицы построены на общем `ui/DataTable`, разделяют `FilterChips`, а фильтры дат используют вынесенный из Strategy Lab общий `ui/DatePicker`.

`/api/live-trading/positions` и `/api/live-trading/dynamics` отделяют данные sandbox-исполнения от paper trading. Эндпоинты поддерживают фильтры тикера, дат и статуса; специальное значение `status=closed` выбирает закрытия по stop и take. `/api/notifications/status` выполняет read-only проверку Telegram `getMe` и кеширует результат на 30 секунд. Реквизиты в ответ не попадают.

## 16. AND-фильтры SignalEngine в StrategyEvaluator

Задача #79 подключает десять классов `BasePattern` вкладки Signals к `StrategyEvaluator` как AND-фильтры после `levels_reversal` (stop/take по-прежнему только от levels). Путь фиксирован и не смешивается:

- `signal_4h_buy` по-прежнему читает `trading.signals` (агрегат 4h BUY). Его не рефакторят.
- id SignalEngine считаются **inline** через `SignalEngine.process_dataframe` / `BasePattern.evaluate` по `trading.indicators` выбранного HTF. Lookup `trading.signals` по `pattern_name` не используется.
- `rsi_oversold` остаётся 1min-фильтром RSI<30 и не заменяет `MR_RSI_Reversal`.

`timeframe` — select, как `level_timeframe`. Поддерживаемые ТФ совпадают с порогами SignalEngine: 30min, 1h, 2h, 4h, 1d, 1w (по умолчанию 4h). Полные схемы Lab живут в `SIGNAL_ENGINE_PATTERN_SCHEMAS` / `PATTERN_REGISTRY` (`pattern_registry.py`); числовые дефолты — 4h `get_thresholds` (для PA — литералы `evaluate`). `normalize_patterns` сохраняет эти параметры, а `StrategyEvaluator` сейчас ключует inline evaluate только по `timeframe`. Фильтр смотрит последнюю *закрытую* HTF-свечу (open бара + длительность ТФ <= текущий 1min ts), чтобы бэктест не заглядывал в ещё формирующийся бакет. Отсутствующие HTF-индикаторы отклоняют вход. `2h` есть в контракте, потому что у паттернов есть пороги, но пайплайн свечей/индикаторов сейчас 2h не пишет — выбор этого ТФ не даёт сделок.

`build_strategy_context` заранее считает BUY-метки по каждому включённому фильтру и передаёт `signal_filter_series` в `StrategyEvaluator.load_context` (бэктест, paper, live). Дефолт `levels_reversal` + `signal_4h_buy` (включая locked `test_20260731`) не включает ни один SignalEngine id, поэтому список сделок не меняется. E2E: `tests/test_signal_pattern_e2e.py` (`levels_reversal` + один id из реестра, например `PA_Engulfing` на 4h).
