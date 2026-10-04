# Конвейер данных

> **Source:** project-context.ru.md sections 4
> **Last refreshed:** 2026-10-04, task-346

## 4. Конвейер данных

**Historical / refresh** (`data_refresher.py`, background, каждые 15 мин, `get_streaming_universe()` = top-15 ∪ LIVE_UNIVERSE):
MOEX ISS API -> candles_1min_raw (incremental) -> candles_aggregated (30min/1h/4h/1d, incremental) -> indicators (30min/1h/4h/1d) -> signals (30min/1h/4h/1d). Поддерживает актуальность 4h BUY-сигналов для рукава base_4hbuy.

**Streaming** (`online_data.py`, background): T-Bank streaming -> online_candles_1min + online_orderbook_aggregates.

**Paper trading** (`live_engine.py` + `paper_trader.py`, background):
- live_engine: читает активную стратегию из БД (`paper_strategy.get_active_paper_strategy`), строит 4h контекст через `build_strategy_context`, передаёт живые 1min бары в per-ticker `StrategyEvaluator` (единая логика входа, та же что в бэктесте), генерирует сигналы в `trading.alerts`.
- paper_trader: читает конфиг стратегии из БД (RR из `config.risk_reward`, трейлинг из `config.trailing_stop`), alerts -> market positions -> мониторинг stop/take/trailing (динамически обновляет `stop_price` через in-memory `TrailingState`, фиксирует причину выхода `trailing`) -> запись equity. Записывает `strategy_name` в `paper_positions` и в best-effort режиме отправляет Telegram alerts для открытий, закрытий, stop/take, пересечения порога drawdown и GAME OVER.
- При старте `start_processes.sh` запускает `position_catchup.py` (разбор pending + проверка open по историческим 1min свечам).

**Sandbox live execution** (`live_executor.py`, опциональный фоновый процесс): использует тот же `StrategyEvaluator` и live 1min контекст, затем требует окно входа MOEX [10:00, 19:00) МСК, свежий imbalance стакана, проверяет sandbox-баланс, рассчитывает целое число лотов, выставляет market BUY и записывает позицию в `trading.live_positions`. Overnight-запуск: `START_LIVE_EXECUTOR=1 ./start_processes.sh` (без `DURATION_MINUTES`) ждёт 10:00 МСК, входит только до 19:00 и держит стоп/тейк до закрытия позиции по цене. Обычный paper-запуск не выставляет брокерские ордера.

**Strategy Lab** (`strategy_jobs.py`): UI Lab всегда пишет `config.strategy_name = "levels_reversal"`, поэтому full-sample идёт через `run_portfolio_backtest` (plugin), а не `run_strategy_backtest`. В plugin `MarketContext` должен быть `htf_bars` из `build_strategy_context`, иначе `LevelsTracker` не видит закрытые HTF-бары (задача #116). Walk-forward по-прежнему через `run_walkforward`. JSONB метрик санируется (`inf`/`nan` → `null`) перед INSERT в `backtest_results`.
