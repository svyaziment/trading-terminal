# Руководство по передаче контекста агента: Trading Terminal

Последнее обновление: 2026-08-30 (задача #137 автономный overnight LiveExecutor; синхронизировано с английской версией). Сопутствующий файл: `project-context.ru.md` (английский оригинал: `project-context.md`).
Этот файл — операционное руководство для агентов. Сначала прочитайте `project-context.ru.md` / `project-context.md`, чтобы понять архитектуру.

## 1. Назначение

Операционные знания для безопасной работы с проектом: структура, схема БД, pipeline, API, известные проблемы, roadmap, операционные тонкости и протокол сотрудничества (сбор контекста перед multi-element задачами).

## 2. Структура проекта

Полное дерево см. в разделе 2 `project-context.ru.md`. Ключевые операционные точки входа:
- `backend/app/main.py` - приложение FastAPI + регистрация маршрутов.
- `backend/app/analytics/trading_config.py` - торговая вселенная, LIVE_UNIVERSE (PO-список задачи #135) и реестр стратегий (единый источник истины).
- `start_processes.sh` / `stop_processes.sh` - процессы paper trading и опционального sandbox-исполнения.
- `docs/refresh/context_collector.py` - сборщик контекста для задач агента.

## 3. Схема базы данных

См. раздел 3 `project-context.ru.md`. Новые таблицы включают `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `live_positions`, `trading_universe` и `alerts`. Все находятся в схеме `trading`.

## 4. Конвейер данных

См. раздел 4 `project-context.ru.md`. По умолчанию запускаются четыре paper-процесса:
1. `data_refresher` - MOEX 1min + агрегация + индикаторы + сигналы (каждые 15 минут, top-15 ∪ LIVE_UNIVERSE).
2. `online_data` - стриминг 1min свечей + стакана.
3. `live_engine` - читает активную стратегию из БД (`paper_strategy.get_active_paper_strategy`), строит 4h контекст через `build_strategy_context`, передаёт живые 1min бары в per-ticker `StrategyEvaluator` (единая логика входа, та же что в бэктесте), генерирует сигналы в `trading.alerts`.
4. `paper_trader` - читает конфиг стратегии из БД (RR из `config.risk_reward`), alerts -> market позиции (open по best_ask, один arm) -> мониторинг stop/take -> запись equity и best-effort Telegram-уведомления. Записывает `strategy_name` в `paper_positions`.
- Опционально: `LiveExecutor` выполняет sandbox-ордера через брокера и запускается только с `START_LIVE_EXECUTOR=1`.
При старте: `position_catchup` разбирает pending/open позиции по историческим свечам.

## 5. API Endpoints

См. раздел 5 `project-context.ru.md`. Strategy Lab: `/api/strategies/*`. Paper trading: `/api/paper-trading/*`.

## 6. Паттерны

См. раздел 6 `project-context.ru.md`.

## 7. Известные проблемы и статус

См. раздел 7 `project-context.ru.md`.

## 8. Статус roadmap

См. раздел 8 `project-context.ru.md`.

## 9. Важные замечания

См. раздел 9 `project-context.ru.md`.

## 10. Операционные тонкости

- **MSYS path conversion**: для docker-команд с абсолютными путями в Git Bash используйте `MSYS_NO_PATHCONV=1`. Без этого `/app/...` превращается в `C:/Program Files/Git/app/...`.
- **Windows Python vs MSYS paths**: хостовой `python`/`python3` не может открывать MSYS-абсолютные пути (`/f/GIT/...`). Передавайте Python-скриптам/патчам ОТНОСИТЕЛЬНЫЕ пути (относительно корня репозитория) или запускайте Python внутри контейнера.
- **stdout pollution**: DBManager пишет логи в stdout. В скриптах, которые парсят JSON из stdout, перенаправьте логирование в stderr ДО импорта app. Иначе: "Extra data: line 1 column 5 (char 4)".
- **Экранирование %% в SQL**: psycopg2 воспринимает `%` как начало placeholder. Экранируйте modulo как `%%`.
- **close_pool()**: никогда не вызывайте `db.close_pool()` в FastAPI-хендлерах или долгоживущих фоновых циклах (pool на весь процесс). Только в standalone-скриптах, которые завершаются. В `data_refresher` pool живёт между циклами.
- **Heredoc loss**: большие bash heredocs могут терять блоки при копировании в Git Bash. Всегда проверяйте размер файла после создания (`wc -c`). Если байт меньше ожидаемого, повторите копирование.
- **Буферизация логов**: фоновые процессы (start_processes.sh) используют `python -u` + `logging.basicConfig(level=INFO, stream=sys.stdout)` для немедленной записи логов в файлы. Без этого логи блочно буферизуются и кажутся пустыми до заполнения буфера.
- **Overnight-длительность LiveExecutor (задача #137)**: без `DURATION_MINUTES` `start_processes.sh` поднимает paper до **следующего открытия после 19:00**, чтобы стоп/тейк после закрытия сессии ещё видели стакан. LiveExecutor ждёт 10:00 и **входит только в [10:00, 19:00)**; выход по цене, в том числе после 19:00. Оставленный `DURATION_MINUTES=540` при старте в 23:00 всё ещё умрёт в 08:00. Для canary по-прежнему нужен явный `DURATION_MINUTES=N`. Unit: `cd backend && python -m pytest -q tests/test_moex_session.py tests/test_live_executor.py`.
- **Docker rebuild**: после изменений backend-кода ОБЯЗАТЕЛЬНО пересоберите (`docker compose up -d --build backend`).
- **JSON NaN / Infinity**: pandas создаёт NaN/NaT, а одна выигрышная сделка даёт `pf: Infinity`. Python `json.dumps` пишет нестрогий JSON, который PostgreSQL JSONB отклоняет (`invalid input syntax for type json`). Санируйте API-ответы **и** INSERT в `backtest_results` через `_json_dumps` → `_json_safe` в `strategy_jobs.py` (`inf`/`nan` → `null`). То же для API-ответов в `paper_trading_jobs.py`. Timestamp в SQL приводите к тексту (`created_at::text`).
- **JSONB as string**: DBManager возвращает JSONB-колонки как Python-repr строки, а не dict. Нормализуйте через `_to_dict` (json.loads, затем fallback ast.literal_eval).
- **Backtest matrix runtime**: полная матрица занимает ~10-15 мин. Для liveness используйте quick=true.
- **Reports mount**: backend монтирует `./reports` (docker-compose). Прогоны стратегий пишут `reports/strategy-lab/last_run.json` - отправляйте его при любой ошибке Strategy Lab.
- **Вето зоны сопротивления (задача #97)**: `levels_reversal` не должен входить, если 1min close лежит в активной зоне сопротивления, даже если `nearest_level_at(..., 'support')` вернул валидную поддержку и расширение 0.5×ATR покрывает fill. Это структурный дефект, а не role-reversal (ALRS paper #711: fill 19.80 внутри импульсного сопротивления 19.67). Гард: `overlapping_resistance_zone_at` в `StrategyEvaluator.check_entry`. Задача #106: та же функция пропускает зоны не в `active`, если есть колонка `state`. Задача #107: `StrategyEvaluator` передаёт `LevelsTracker` (и `is_broken`) в вето только если включён `level_breakout_retest`; locked `test_20260731` его не включает, поэтому paper/live вето не меняется. Locked `test_20260731` не перезаписывать. Unit: `cd backend && python -m pytest -q tests/test_resistance_zone_veto.py tests/test_levels_state_machine.py tests/test_level_breakout_retest.py`.
- **Lab plugin HTF (задача #116)**: Strategy Lab всегда пишет `config.strategy_name = "levels_reversal"`, поэтому `_run_job` вызывает `run_portfolio_backtest`, а не `run_strategy_backtest`. В `MarketContext.htf_bars` должен лежать `build_strategy_context()['htf_bars']` (тот же ТФ, что и уровни). `candles_4h` на этом пути обычно пуст. Без HTF `LevelsTracker._sync_tracker` выходит сразу, все уровни остаются `active`, любой паттерн пробоя (`level_breakout_retest`, `levels_sr_breakout`) даёт **ноль сделок**. Locked `test_20260731` пробой не включает — прокидывание HTF для paper no-op. Unit: `cd backend && python -m pytest -q tests/test_strategy_plugin.py tests/test_level_breakout_retest.py`.
- **Композитный S/R (задача #117)**: `levels_sr_breakout` — **движок входа** (OR пути A поддержка и пути B ретест сопротивления), не AND-фильтр. Изолированный прогон Lab: в `config.patterns` есть `levels_sr_breakout` (и опционально `signal_4h_buy`) **без** `levels_reversal`. Если оба чипа включены — побеждает композит (один support-путь). Не AND с `level_breakout_retest` как заменой. Сделки несут `source` (`levels_sr_breakout_support` / `levels_sr_breakout_resistance`). Locked `test_20260731` этот id не включает. Unit: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.
- **AFKS smoke (задача #119)**: изолированный тикер, не портфель 50k. Пакет `analytics/issue-119-afks-sr-breakout-smoke/`. A vs B на AFKS `2024-08-01`…`< 2026-08-21`. B-support может быть больше A: композит передаёт `LevelsTracker` в вето. `source` есть у `run_strategy_backtest`; `run_portfolio_backtest` его сейчас теряет. Не lock/overwrite `test_20260731` / `test_20260820` / `test_20260821`. Повтор: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py` (нужен `results.json`). Unit: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.
- **Lab-вселенная A/B (задача #124)**: изолированный прогон 28 тикеров `get_big_tickers`, те же SHA, что #119. Пакет `analytics/issue-124-sr-breakout-universe/`. A n=2559 PF 1.46; B n=4799 PF 1.39 (support 3811 / resistance 988). AFKS совпал с #119; бар ALRS 19.80 отсутствует. Опциональный портфель 50k по B — отдельный блок, не isolated PF. Не lock/overwrite три эталонные стратегии. Повтор: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Unit: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.
- **Поддержка с трекером (задача #127 / эпик #126)**: `levels_sr_support` — **только B-support** из #124. Та же геометрия поддержки, что у `levels_reversal`, плюс вето #97 **с** `LevelsTracker`. Без `check_breakout_retest`. Изолированный прогон: в `config.patterns` есть `levels_sr_support` (опционально `signal_4h_buy`) **без** `levels_reversal` / `levels_sr_breakout` / `level_breakout_retest`. Композит по-прежнему побеждает, если оба движка включены. Не включать трекер «молча» на locked `test_20260731`. Isolated Lab-вселенная: handover §31 (C n=4380 PF 1.45; exclusive 3811/1.51 не bit-for-bit). Unit: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_levels_sr_breakout.py tests/test_resistance_zone_veto.py tests/test_strategy_plugin.py`.
- **Isolated support-вселенная (задача #129)**: пакет `analytics/issue-129-sr-support-universe/`. Isolated C (`levels_sr_support` + `signal_4h_buy`) на той же 28-тикерной Lab-вселенной, что #124. Exclusive B-support 3811 / 1.51 — подпись композита (путь B занимает слот). Runnable C — 4380 / 1.45. Extra 611: occupancy 610 + leftover 1; missing 42 cascade. AFKS 89 / 1.49 (exclusive 78 ⊆ C). Resistance n=0. Бар ALRS 19.80 заблокирован. #130 должен брать C, не exclusive. Не lock/overwrite три эталонные стратегии. Повтор: `python analytics/issue-129-sr-support-universe/analysis.py`. Unit: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.
- **Портфель поддержки 50k (задача #130)**: пакет `analytics/issue-130-sr-support-portfolio/`. Replay слотов published C (4380 кандидатов, SHA `3b7864c4…aedb1b`), не exclusive 3811/1.51 и не фильтр `source=` из #124 B-mix. n=3237 PF 1.33 equity 96 204.63 daily Max DD 6.08% без GAME OVER. Бар ALRS 19.80 отсутствует. Вердикт: не paper. Повтор: `python analytics/issue-130-sr-support-portfolio/analysis.py`. Unit: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.

## 11. Протокол сотрудничества (агенты)

- **Собирайте контекст перед multi-element задачами.** Если задача затрагивает несколько модулей/классов/скриптов или их взаимодействие, СНАЧАЛА соберите актуальный контекст из первоисточников, а не угадывайте реализацию:
python docs/refresh/context_collector.py
--task-id task-NNN
--files backend/app/analytics/levels_backtest.py,backend/app/db/db_manager.py
--tables backtest_runs,backtest_trades
--output reports/task-NNN/context.json
  `--files` собирает содержимое файлов; `--tables` собирает схему + число строк + sample + диапазон дат. Загрузите полученный `context.json` перед реализацией.
- **Скрипты задач живут в `scripts/`** (gitignored). Каждая задача пишет отчёты в `reports/<AGENT_NAME>/<ISSUE_NUMBER>_<ISSUE_NAME>/` (см. developer-sop.md для конвенций именования).
- **Проверяйте после записи**: всегда проверяйте размеры файлов (`wc -c`) и выполняйте build/health check после изменений.
- **Документация двуязычная**: держите `*.md` и `*.ru.md` синхронно (project-context, handover, strategy docs).

## 12. Эксплуатация фильтра imbalance стакана

- Точки входа: `online_data.save_orderbook_aggregate` рассчитывает и сохраняет каждое streaming-обновление; `orderbook_imbalance.get_recent_imbalance` читает свежий агрегат; `passes_imbalance_filter` служит обязательным шлюзом сигнала.
- Инфраструктурная политика находится в `ORDERBOOK_IMBALANCE` файла `trading_config.py`: глубина 10, максимальный возраст 5 минут, порог по умолчанию 1.0. Переопределение стратегией: верхнеуровневый `config.imbalance_threshold`.
- Условие прохождения строгое: `volume_imbalance > imbalance_threshold`. Отсутствующие, устаревшие, null, NaN/бесконечные данные и нулевая ask depth всегда отклоняют сигнал.
- Быстрая диагностика БД:
  `SELECT ticker, timestamp, bid_depth, ask_depth, volume_imbalance FROM trading.online_orderbook_aggregates ORDER BY timestamp DESC LIMIT 20;`
- Если все live-сигналы пропускаются, сначала убедитесь, что `online_data` запущен, а последняя строка моложе 5 минут. Не ослабляйте защиту от отсутствующих данных.
- Unit-тест: `cd backend && python -m pytest -q tests/test_orderbook_imbalance.py`.

## 13. Работа с клиентом T-Bank Sandbox

- Точка входа: `app.broker.tinkoff_sandbox.TinkoffSandboxClient`. Всё исполнение брокерских ордеров должно оставаться за этим классом; downstream executor не должен создавать или вызывать production-сервис `orders`.
- Обязательное окружение: отдельный sandbox-токен `TINVEST_SANDBOX`. Необязательный `TINVEST_SANDBOX_ACC` фиксирует sandbox-счёт; иначе выбирается первый открытый. Клиент намеренно никогда не использует реквизиты рыночных данных `TINVEST_TOKEN` / `TINVEST_ACC`. Обнаружение счёта не открывает и не пополняет его.
- Read-only smoke check:
  `cd backend && python -c "from app.broker.tinkoff_sandbox import TinkoffSandboxClient; print(TinkoffSandboxClient().check_balance())"`
- Market-ордер: передайте `instrument_id`, положительный целый `quantity` в лотах и при необходимости `direction` (`buy`/`sell`). `price` передавать нельзя.
- Limit-ордер: передайте те же поля, а также `order_type="limit"` и положительный `price`. В `instrument_id` используйте UID/FIGI инструмента, который принимает T-Bank.
- Для отмены нужен брокерский `order_id`, возвращённый `execute_order`.
- Retry-политика берётся только из `SANDBOX_TRADING` в `trading_config.py`. Не добавляйте отдельные retry-циклы вокруг `execute_order`: клиент уже повторяет временные gRPC-ошибки с тем же idempotency key.
- Клиент не открывает sandbox-счёт и не зачисляет на него 50 000 RUB из эпика автоматически. Создание и пополнение — явная операция пользователя. Никогда не печатайте токены и не коммитьте `.env`.
- Unit-тест: `cd backend && python -m pytest -q tests/test_tinkoff_sandbox.py`.

## 14. Эксплуатация расчёта размера позиции

- Точка входа: `app.analytics.position_sizer.calculate_position_size`. Передавайте свободный капитал, расстояние до stop-loss в процентах от входа, цену входа и `lot_size` инструмента.
- Live-значения берутся только из `POSITION_SIZING` файла `trading_config.py`: риск 1% на сделку и максимум 20% капитала в одной позиции. Необязательные переопределения функции предназначены для тестов и симуляций.
- В broker order передавайте `size_lots`. `size_rub` — бюджет до округления, а не указание на дробное количество лотов.
- Результаты `invalid_stop` и `insufficient_capital` означают отказ (`size_lots == 0`) и не должны доходить до брокера. `min_lot` можно исполнять: калькулятор уже убедился, что свободного капитала хватает на один полный лот.
- Unit-тест: `cd backend && python -m pytest -q tests/test_position_sizer.py`.

## 15. Эксплуатация sandbox live executor

- Условия запуска: backend пересобран, streaming online data работает, активна одна заблокированная стратегия, sandbox-счёт пополнен и `LIVE_TRADING.enabled=true`.
- При подготовке новой БД примените миграцию явно: `psql ... -f backend/migrations/20260817_01_live_positions.sql`. `LiveExecutor.initialize()` также автоматически применяет ту же идемпотентную схему.
- Безопасный overnight-запуск (задача #137): пересобрать backend, затем `START_LIVE_EXECUTOR=1 ./start_processes.sh` **без** `DURATION_MINUTES`. Paper-процессы живут до ближайшего будничного **10:00 после 19:00 этой сессии** (стрим для оставшихся стоп/тейк). LiveExecutor спит до 10:00 МСК, **входит только 10:00–19:00**, затем держит стоп/тейк до закрытия позиции по цене. Часы — системные, приведённые к МСК (UTC+3). `START_LIVE_EXECUTOR=1` остаётся opt-in, чтобы обычный paper-запуск не выставлял sandbox-ордера. Лог: `reports/live-executor/executor.log`.
- Canary / фиксированное окно: `DURATION_MINUTES=N` по-прежнему стартует сразу и останавливается через N минут после запуска. Так нельзя запускать overnight вс.→пн.
- Входы LiveExecutor только в [10:00, 19:00) МСК (`reason=outside_entry_window`), даже если `StrategyEvaluator.entry_window` равен 7–19. Стоп/тейк срабатывают по цене и после 19:00; процесс сам останавливается только когда позиций не осталось (или по SIGTERM). Политика shutdown без изменений (`close_positions_on_shutdown=false`).
- Порядок обработки фиксирован: BUY от `StrategyEvaluator` -> окно сессии -> свежий imbalance -> свободные RUB -> position sizing -> market BUY -> take sell-limit -> запись/сверка БД.
- Stop-защита синтетическая. Нельзя выставлять stop sell-limit при входе: limit ниже рынка исполнился бы сразу. Монитор ждёт `current_price <= stop_price`, отменяет take и затем выставляет sell-limit по наблюдаемой цене.
- Каждая физическая попытка broker API, включая retry SDK и обнаружение счёта, использует один token bucket (`api_rate_limit`, максимум 10/сек). Не добавляйте вызовы в обход `_broker_call` или клиентского hook `before_request`.
- SIGTERM/SIGINT запрашивает очистку. Ожидающие entry/protection заявки отменяются; открытые позиции закрываются только при `close_positions_on_shutdown=true`. При значении false по умолчанию позиции остаются открытыми, а их protection IDs очищаются в БД.
- Причины отказа BUY читайте в `reports/live-executor/executor.log`. Каждая запись `Live signal skipped` содержит `ticker=<тикер>`, стабильный `reason=<код>` и релевантные значения. Ожидаемые коды фильтров и лимитов: `outside_entry_window`, `stale_or_missing_orderbook`, `imbalance_below_threshold`, `insufficient_cash`, `invalid_stop`, `insufficient_capital`, `max_open_positions` и `broker_error`; `min_lot` по контракту sizing остаётся исполнимым. Например, `reason=imbalance_below_threshold imbalance=0.9 imbalance_threshold=1.0` означает, что стрим работает, но фильтр отклонил вход, а `reason=stale_or_missing_orderbook orderbook_age_seconds=missing` — что данных стакана нет. Эти записи появляются только после BUY-решения от `StrategyEvaluator`; отсутствие записей об отказах может означать, что BUY-сигналов не было. Для broker errors логируются только операция и тип исключения, без реквизитов счёта, credentials и текста исключения.
- Диагностика: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Тесты: `cd backend && python -m pytest -q tests/test_live_executor.py tests/test_moex_session.py`.

## 16. Эксплуатация Telegram alerts для paper trading

- Точка входа: `app.notifications.telegram_notifier.TelegramNotifier`; интеграция с paper trading находится в `paper_trader.py`.
- Задайте `TGM_TOKEN` и `TGM_CHAT`; прежнее имя `TGM_CHAT_ID` сохранено как fallback. `TGM_APP_ID` и `TGM_APP_HASH` загружаются, но Bot API их не использует. Никогда не печатайте и не коммитьте эти значения.
- Notifier отправляет Markdown-сообщения об открытии/закрытии с тикером, BUY/SELL, ценой, количеством лотов и штук, PnL и причиной. Stop/take имеют отдельные эмодзи, критические события — 🚨.
- Вызовы сериализуются с частотой не более одной попытки в секунду. Ошибки доставки только логируются и не должны останавливать paper trading.
- Alert большого drawdown использует `risk.max_daily_loss_pct` и отправляется только при пересечении порога. GAME OVER отправляется только при первом переходе equity в неположительное значение.
- Тесты: `cd backend && python -m pytest -q tests/test_telegram_notifier.py tests/test_paper_trader_notifications.py`.

## 17. Эксплуатация панели Live Trading

- Откройте вкладку frontend `Live Trading`. Sandbox-данные из `trading.live_positions`, динамика PnL и статус Telegram обновляются каждые 10 секунд.
- `current_price` открытой позиции берётся из последнего best bid стакана с fallback на best ask. При отсутствии рыночных данных UI показывает недоступное значение, а не подставляет устаревшую цену.
- Обе таблицы используют единый `DataTable` и общие filter chips, как в Strategy Lab. Фильтры открываются в заголовках; диапазоны дат используют общий календарный `DatePicker`. История поддерживает серверную сортировку и пагинацию; отсутствие точного фильтра статуса означает все закрытые позиции (`closed_stop` и `closed_take`).
- `/api/notifications/status` выполняет Telegram `getMe` без отправки сообщения и кеширует результат на 30 секунд. `configured=false` означает отсутствие `TGM_TOKEN` или chat ID; `configured=true` вместе с `disconnected` означает ошибку проверки Bot API.
- Проверка frontend: `cd frontend && npm run build`. Backend: `cd backend && python -m pytest -q tests/test_live_trading_api.py tests/test_notifications_api.py tests/test_telegram_notifier.py`.

## 18. Эксплуатация live-вселенной

- Рейтинг paper остаётся `get_trading_universe()` (top-15 из `trading.trading_universe`). Таблицу не сужать.
- Streaming и data refresh используют `get_streaming_universe()` = top-15 ∪ `LIVE_UNIVERSE`.
- Sandbox-исполнение использует `LIVE_UNIVERSE` / `get_live_trading_universe()`: ROSN, IRAO, AFKS, NVTK, SBER, MTSS, PHOR, MOEX, FLOT (PO-список задачи #135). Геттер **не** обрезает имена вне paper top-15. `LiveExecutor.initialize()` пересекает тикеры paper-стратегии с этим списком.
- Исторический рейтинг задачи #66 (SBER, LKOH, RUAL, NVTK, GAZP) живёт в `analytics/issue-66-live-universe/`. Не переписывать тот пакет под текущий PO-список.
- Имя locked paper для preflight: `EXPECTED_LOCKED_STRATEGY` = `test_20260830_new_level`.
- Тесты: `cd backend && python -m pytest -q tests/test_trading_config.py tests/test_live_universe_analysis.py tests/test_live_executor.py`.

## 19. Первая canary-сессия sandbox LiveExecutor

Используйте окно 60-120 минут во время торговой сессии MOEX, предпочтительно с 10:00 до 16:00 МСК. Цель — проверить цепочку исполнения, а не доходность. Для canary запрещено использовать историческое значение по умолчанию `DURATION_MINUTES=1200`.

1. После слияния изменений live-вселенной и логов отказов пересоберите backend: `docker compose up -d --build backend`. Убедитесь, что `http://localhost:8000/health` возвращает `status=ok`.
2. Оставьте запущенными ровно по одному процессу `data_refresher`, `online_data`, `live_engine` и `paper_trader`. Не останавливайте paper trading ради canary. Если они не запущены, заранее поднимите обычный paper-стек с явной длительностью, покрывающей окно canary.
3. Непосредственно перед executor выполните read-only preflight:
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   Проверка завершится ошибкой, если backend нездоров, `LIVE_UNIVERSE` отличается от SBER/LKOH/RUAL/NVTK/GAZP, единственная locked paper-стратегия — не `test_20260731`, нет свободных sandbox RUB, хотя бы один из пяти стаканов старше пяти минут, paper-процессы запущены не в единственном экземпляре, в `trading.trading_universe` не 15 строк или `allow_real_trading` не равен false.
4. Запустите executor на один час без перезапуска paper-процессов:
   `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 DURATION_MINUTES=60 ./start_processes.sh`.
   Режим `PRESERVE_PAPER_PROCESSES=1` аварийно завершит запуск, если не обнаружит ровно по одному экземпляру каждого из четырёх paper-процессов.
5. За первые пять минут в `reports/live-executor/executor.log` должна появиться строка `Sandbox LiveExecutor started` с числом инициализированных тикеров. Строки отказов появляются только после BUY-решения; их отсутствие может означать, что `StrategyEvaluator` не создал BUY. Также следите за вкладкой Live Trading и `trading.live_positions`.
6. По истечении указанного времени executor остановится автоматически. Для досрочной остановки отправьте SIGTERM только процессу, команда которого содержит `LiveExecutor`; не перезапускайте весь paper-стек. При `close_positions_on_shutdown=false` позиции в sandbox останутся открытыми, а ожидающие/protection-ордера будут отменены и их IDs очищены в БД — сразу проверьте оставшиеся позиции.
7. Длительность paper-стека должна покрывать canary с запасом. Если paper-процесс истечёт во время сессии, перезапустите только четыре paper-воркера; не вызывайте полный `start_processes.sh`, потому что шаг 0 убьёт `LiveExecutor`.

Полезные read-only SQL:

```sql
SELECT id, name, in_paper_test, locked
FROM trading.strategies
WHERE in_paper_test=true AND locked=true;

SELECT ticker, max(timestamp) AS latest_orderbook
FROM trading.online_orderbook_aggregates
WHERE ticker IN ('SBER','LKOH','RUAL','NVTK','GAZP')
GROUP BY ticker ORDER BY ticker;

SELECT count(*) AS universe_size
FROM trading.trading_universe;

SELECT timestamp, equity_rub
FROM trading.paper_equity
ORDER BY timestamp DESC LIMIT 2;
```

После остановки зафиксируйте в Issue #74: инициализированные тикеры из startup log; количество отказов по каждому `reason`; число исполненных BUY; последние позиции из запроса ниже; подтверждение, что `paper_equity` обновлялась в том же интервале.

```sql
SELECT ticker, status, size_lots, entry_price, broker_order_id
FROM trading.live_positions
ORDER BY id DESC LIMIT 20;
```

Не меняйте locked-стратегию, RR, порог imbalance и `trading.trading_universe`. Никогда не включайте `allow_real_trading`; запуск выполняется только в sandbox.

## 20. Эксплуатация фильтров SignalEngine в Strategy Lab

- Точки входа: `app.analytics.signal_pattern_filters` (inline evaluate + последний закрытый HTF) и `StrategyEvaluator.check_entry`. Контекст собирает `build_strategy_context`.
- Правило пути: `signal_4h_buy` читает `trading.signals`; десять id SignalEngine вызывают `BasePattern.evaluate` на `trading.indicators`. Не подмешивать lookup по `pattern_name`. Не подменять `MR_RSI_Reversal` фильтром `rsi_oversold`.
- Контракт `timeframe`: `SIGNAL_PATTERN_TIMEFRAME_PARAM` в `pattern_registry.py` (select, 30min/1h/2h/4h/1d/1w, по умолчанию 4h). Полные схемы — в `SIGNAL_ENGINE_PATTERN_SCHEMAS`; дефолты 4h совпадают с текущими `get_thresholds` SignalEngine / литералами `evaluate` у PA. `normalize_patterns` их заполняет; evaluator по-прежнему ключует inline evaluate только по `timeframe`.
- Как включить в конструкторе: добавьте чип SignalEngine из `GET /api/patterns` (не хардкодить десять id в `StrategyLab.tsx`). Чипы сгруппированы по `category` из API с RU-заголовками. Таймфрейм и параметры паттерна задаются в `PatternSettingsModal` (отдельный глобальный TF-селектор не нужен). Save вызывает `normalize_patterns`; тот же конфиг идёт в `strategy_backtest`, paper (`get_active_paper_strategy` → `StrategyEvaluator`) и live. Locked `test_20260731` не перезаписывать. Fallback из двух чипов (`levels_reversal` + `signal_4h_buy`) используется только если API паттернов пуст; живой реестр этим списком не подменяется.
- Фильтр смотрит последнюю закрытую HTF-свечу. Нет строк индикаторов — вход отклоняется. `2h` есть в контракте, но текущий пайплайн агрегации/индикаторов его не пишет.
- Locked paper-стратегия `test_20260731` должна оставаться только `levels_reversal` + `signal_4h_buy`.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_signal_engine_filters.py tests/test_pattern_registry.py tests/test_signal_pattern_e2e.py`.

## 21. Эксплуатация превью паттерна на графике (эпик #87)

- Точка входа: `POST /api/patterns/preview` в `strategy_jobs.py`; логика в `app.analytics.pattern_preview`.
- Запрос: `ticker`, `pattern_id`, draft `params`, `date_from`, `date_to`. ТФ берётся из params (`level_timeframe` для `levels_reversal`, `timeframe` для id SignalEngine).
- Ответ: `status` (`ok` / `empty` / `error` / `unsupported`), `candles`, typed `overlays` (`ray`, `band`, `line`, `marker`). Задача #88 реализует `levels_reversal`: все уровни с `defined_ts` в окне; на каждый уровень — `ray` от `defined_ts` до последнего видимого бара и `band` зоны ATR. Не эмулировать лучи бесконечными price lines.
- Неизвестный `pattern_id` → `status=error` без 500. Нет свечей (в т.ч. неподдерживаемый `2h`) → `status=empty` с понятным сообщением.
- Остальные id паттернов → `status=unsupported` только со свечами, пока #91 не добавит renderer'ы. Frontend — #89–#92.
- Unit-тест: `cd backend && python -m pytest -q tests/test_pattern_preview.py`.

## 22. Эксплуатация state machine уровней

- Точки входа: `LevelsTracker` / `get_levels_with_state` / `is_broken` в `levels_engine.py`. Инициализация из `get_levels()` (алиас `build_levels`). Подавайте бары **того же** ТФ, что и уровни (обычно 4h). Только in-memory — без таблицы и миграции.
- Пороги только из `LEVEL_STATE_MACHINE` в `trading_config.py`: `breakout_buffer_atr=0.25`, `confirm_bars=2`, `min_penetration_atr=0.5`, `zone_extension_atr=0.5`. `zone_extension_atr` документирует текущую ширину зоны `build_levels`; трекер не пересчитывает `zone_lower`/`zone_upper`.
- Пробой сопротивления: последние `confirm_bars` close все выше `zone_upper`, последний close выше `zone_upper + buffer×ATR`, max(window) не ниже `zone_upper + min_penetration×ATR`. Поддержка симметрично ниже `zone_lower`. Первый close обратно внутри нативной зоны после пробоя: `broken_up → flipped_support` / `broken_down → flipped_resistance`. Ложный пробой в этой итерации не возвращается в `active`.
- `overlapping_resistance_zone_at` ветирует только `active` сопротивления, если есть колонка `state`, и пропускает `tracker.is_broken(level_id)`, если передан трекер (задача #107). Передавайте снимок трекера **после** `update()` только по закрытым HTF-барам. Кадры без `state` и вызовы без `tracker` сохраняют поведение задачи #97.
- `StrategyEvaluator` создаёт `LevelsTracker`, если в `config.patterns` есть `level_breakout_retest`, `levels_sr_breakout` или `levels_sr_support`. Locked `test_20260731` не включает ни один. `bars_since_breakout(level_id)` считает HTF-бары с момента подтверждённого пробоя.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_state_machine.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py`.

## 23. Эксплуатация паттерна Level Breakout Retest

- Точки входа: `check_breakout_retest` / `evaluate_level_breakout_retest` в `patterns/level_breakout_retest.py`; AND-фильтр `_check_level_breakout_retest` в `StrategyEvaluator`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/` (затенит `breakout.py`).
- Схема Lab: `PATTERN_REGISTRY['level_breakout_retest']` (также копируется в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для AC задачи #107 / `GET /api/patterns`). Дефолты: `level_timeframe=4h`, `retest_window_bars=20`, `retest_zone_atr=0.5`, `entry_trigger_bullish=true`, `stop_atr=1.0`, `risk_reward=2.0`. Порог бычьего тела `0.6` не настраивается в Lab; живёт в `LEVEL_BREAKOUT_RETEST` в `trading_config.py`.
- Критерии (все обязательны): состояние трекера `broken_up` или `flipped_support`; close в `[level ± retest_zone_atr×ATR]`; close ≥ пробитого `level_price`; `bars_since_breakout <= retest_window_bars`; если `entry_trigger_bullish` — `close > prev_high` ИЛИ бычье тело.
- Stop/take: `stop = entry − stop_atr×ATR`, `take = entry + risk_reward×(entry−stop)`. При включённом паттерне они заменяют levels stop/take; верхнеуровневый RR-фильтр конфига поверх не применяется (RR паттерна уже задаёт отношение).
- Контекст: `build_strategy_context` возвращает `htf_bars` (тот же ТФ, что и уровни). Evaluator подаёт в трекер только HTF-бары, чей close ≤ текущий 1min ts (без lookahead). Paper/live `load_context` / `update_context` прокидывают этот кадр. Lab/plugin-путь: `portfolio_backtest` кладёт тот же кадр в `MarketContext.htf_bars` (задача #116); на `candles_4h` не опираться.
- Взаимодействие с вето: при включённом паттерне пробитое сопротивление больше не opposing zone (`is_broken`). Без паттерна любое перекрывающееся сопротивление по-прежнему ветирует (locked `test_20260731`).
- Компонуемость: AND с `levels_reversal` (по-прежнему обязателен для пути зоны поддержки) и с фильтрами SignalEngine / `signal_4h_buy`. Чип Lab: handover §24. `GET /api/patterns` — источник имён, подсказок, иконки и схемы параметров.
- Locked `test_20260731` не перезаписывать.
- Unit-тесты: `cd backend && python -m pytest -q tests/test_level_breakout_retest.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py`.

## 24. Эксплуатация чипа Level Breakout Retest в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts`, `patternValidation.ts`.
- Включается в группе **Пробой**. Видимое имя — API `label` («Пробой уровня с ретестом»); EN `label_en` («Level Breakout Retest») в tooltip и под заголовком модалки. Иконка `breakout_up` (стрелка через уровень) тоже из API.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить шесть параметров во frontend. Схема: `level_timeframe` (1h/4h/1d), `retest_window_bars` (1–100), `retest_zone_atr` (0.1–2.0), `entry_trigger_bullish`, `stop_atr` (0.5–3.0), `risk_reward` (≥1). Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Комбинировать с `levels_reversal` (по-прежнему нужен для пути зоны поддержки) и опциональными фильтрами SignalEngine / `signal_4h_buy`. Логика AND не меняется. Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: после подтверждённого пробоя сопротивления нужен вход на ретесте (смена роли), а не только от нативной зоны поддержки. На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Этот AND-фильтр **не** замена `levels_sr_breakout` (handover §25). Эпик #115 испытывает композит изолированно; не комбинировать два чипа как «новую стратегию».

## 25. Эксплуатация композитного S/R паттерна (`levels_sr_breakout`)

- Точки входа: `PATTERN_ID` / `source` в `patterns/levels_sr_breakout.py`; OR-логика в `StrategyEvaluator._check_sr_breakout_entry`. Путь B переиспользует `check_breakout_retest`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/`.
- Схема Lab: `PATTERN_REGISTRY['levels_sr_breakout']` (также в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для `GET /api/patterns`). Категория `levels` (рядом с `levels_reversal`, не в breakout). Иконка `support_breakout` (должна отличаться от `breakout_up`). Параметры = все поля `levels_reversal` + поля ретеста (`retest_window_bars`, `retest_zone_atr`, `entry_trigger_bullish`, `stop_atr`, `risk_reward`). Чип Lab: handover §26. Не хардкодить ключи params в TSX.
- Изолированный прогон: в `config.patterns` есть `levels_sr_breakout` и опционально `signal_4h_buy` / id SignalEngine. `levels_reversal` **не** обязателен. `run_strategy_backtest` считает композит достаточным движком входа.
- Порядок в `check_entry` после сессии / HTF / `_sync_tracker`: (1) общие AND (`signal_4h_buy`, SignalEngine, 1min индикаторы); (2) путь B — `check_breakout_retest` → `source=levels_sr_breakout_resistance`, ATR stop/take, без второго RR-фильтра конфига; (3) иначе путь A — зона поддержки + confirm + вето *активного* сопротивления с трекером (`source=levels_sr_breakout_support`, levels stop/take, верхнеуровневый RR). Если сработали оба — побеждает путь B.
- Оба чипа (`levels_reversal` + `levels_sr_breakout`): побеждает композит — один support-путь, без удвоения.
- Не AND с `level_breakout_retest` как заменой этому движку. Контракт AND-фильтра эпика #105 не меняется.
- Трекер / `htf_bars`: тот же корм, что в #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit-тесты не зависят от UI Lab.
- Locked `test_20260731` этот id не включает (paper/live вето и levels stop/take остаются бит-в-бит).
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_sr_breakout.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_level_breakout_retest.py tests/test_strategy_plugin.py`.

## 26. Эксплуатация чипа композитного S/R в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts` (`resolveConfirmWindows`), `patternValidation.ts`. Карта иконок: `PatternIcon.tsx` по API `icon`, не по id паттерна.
- Включается в группе **Уровни** (не **Пробой**). Видимое имя — API `label` («Поддержка + пробой сопротивления»); EN `label_en` («Support Reversal + Resistance Breakout») в tooltip и под заголовком модалки. Иконка `support_breakout` (линия поддержки + пробой сопротивления) тоже из API и должна отличаться от `breakout_up`.
- Этот чип **заменяет** `levels_reversal` для новой стратегии. Изолированный прогон: включить `levels_sr_breakout` и опционально `signal_4h_buy` / SignalEngine; `levels_reversal` и `level_breakout_retest` оставить выключенными. Если оба levels-чипа включены, на backend побеждает композит (один support-путь) — это не третий AND.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить список параметров во frontend. Схема = все поля `levels_reversal` + поля ретеста. Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Верхнеуровневый `config.confirm_windows` берётся из включённой схемы, у которой есть этот param; композит побеждает `levels_reversal` (как backend `_LEVELS_CONFIRM_PATTERN_IDS`). Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: нужен один Lab-движок, который входит и от нативной поддержки, и на подтверждённом ретесте сопротивления. На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Изолированный AFKS smoke (задача #119): handover §27. Lab-вселенная A/B (задача #124): handover §28. Движок только поддержки (задача #127): handover §29. Чип Lab (задача #128): handover §30. Isolated support-вселенная (задача #129): handover §31. Портфель 50k (задача #130): handover §32. Не считать ни один пакет вердиктом для paper.

## 27. Эксплуатация AFKS smoke композита

- Пакет: `analytics/issue-119-afks-sr-breakout-smoke/`. Изолированный тикер, не слоты 50k.
- A = `levels_reversal` + `signal_4h_buy` (та же геометрия, что #103). B = только `levels_sr_breakout` + `signal_4h_buy`. Период `2024-08-01` … `timestamp < 2026-08-21`.
- Основной движок — `run_strategy_backtest`, чтобы на сделках остался `source`. Plugin-путь Lab после #116 совпадает по n/PF, но `source` с plugin-сделок сейчас теряется.
- B-support может быть больше A: композит передаёт `LevelsTracker` в вето, поэтому пробитое сопротивление больше не режет support-вход.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без БД: `python analytics/issue-119-afks-sr-breakout-smoke/analysis.py`. Полный пересчёт: `python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py`.
- Unit: `cd backend && python -m pytest -q tests/test_issue119_analysis.py`.

## 28. Эксплуатация Lab-вселенной композита A/B

- Пакет: `analytics/issue-124-sr-breakout-universe/`. Изолированная Lab-вселенная 28 тикеров (`get_big_tickers`), не live top-5 и не `run_params.tickers`.
- Конфиги A/B — опубликованная пара SHA из #119. Период `2024-08-01` … `timestamp < 2026-08-21`. Движок — `run_strategy_backtest`, чтобы на сделках остался `source`.
- Isolated B больше A по двум причинам: путь B (`levels_sr_breakout_resistance`) и extra support после вето с трекером. Не считать n support-пути B бит-в-бит копией A.
- Опциональный replay B на 50k / 10k / max 5 — **отдельный** блок. Не смешивать этот PF/equity с isolated PF по тикерам. Это не вердикт для paper.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без нового бэктеста: `python analytics/issue-124-sr-breakout-universe/analysis.py`. Полный пересчёт: `python analytics/issue-124-sr-breakout-universe/extract_inputs.py` (резюмируется).
- Unit: `cd backend && python -m pytest -q tests/test_issue124_analysis.py`.

## 29. Эксплуатация паттерна «поддержка с трекером» (`levels_sr_support`)

- Точки входа: `PATTERN_ID` / `SOURCE` в `patterns/levels_sr_support.py`; только support-путь в `StrategyEvaluator._check_sr_support_entry`. Это не SignalEngine `BasePattern` — не добавлять id в `SIGNAL_ENGINE_PATTERN_IDS`. Не класть файл в `patterns/breakout/`.
- Схема Lab: `PATTERN_REGISTRY['levels_sr_support']` (также в `SIGNAL_ENGINE_PATTERN_SCHEMAS` для `GET /api/patterns`). Категория `levels` (рядом с `levels_reversal` и `levels_sr_breakout`, не в breakout). Иконка `support_tracker` (должна отличаться от `breakout_up` и `support_breakout`). Параметры — **только** поля `levels_reversal`, без ключей ретеста. Чип Lab: handover §30. Не хардкодить ключи params в TSX.
- Изолированный прогон: в `config.patterns` есть `levels_sr_support` и опционально `signal_4h_buy` / id SignalEngine. `levels_reversal` **не** обязателен. `run_strategy_backtest` считает этот id достаточным движком входа.
- Порядок в `check_entry` после сессии / HTF / `_sync_tracker`: (1) если включён `levels_sr_breakout` — побеждает композит (без изменений); (2) иначе если включён `levels_sr_support` — общие AND, затем зона поддержки + confirm + вето *активного* сопротивления с `tracker=self._tracker` → `source=levels_sr_support`, levels stop/take, верхнеуровневый RR. **Не** вызывать `check_breakout_retest` на этом id.
- Оба чипа (`levels_sr_support` + `levels_sr_breakout`): побеждает композит. `levels_sr_support` + `levels_reversal`: побеждает новый id (один support-путь, без удвоения). Порядок `_LEVELS_CONFIRM_PATTERN_IDS`: композит > поддержка-с-трекером > `levels_reversal`.
- Отличие от `levels_reversal`: трекер передаётся в вето, пробитое сопротивление больше не режет валидный support-вход. Отличие от `levels_sr_breakout`: нет пути B / ретеста.
- Трекер / `htf_bars`: та же подача, что #107/#116 (`load_context(htf_bars=...)` / Lab plugin `MarketContext.htf_bars`). Unit-тесты не зависят от Lab UI.
- Locked `test_20260731` этот id не включает (paper/live вето и levels stop/take остаются бит-в-бит).
- Unit-тесты: `cd backend && python -m pytest -q tests/test_levels_sr_support.py tests/test_pattern_registry.py tests/test_resistance_zone_veto.py tests/test_levels_sr_breakout.py tests/test_strategy_plugin.py`.

## 30. Эксплуатация чипа «поддержка с трекером» в Lab

- Точки входа: `StrategyLab.tsx` (чипы по `category` из API) и `PatternSettingsModal.tsx` (поля из `PatternDef.params`). Хелперы: `patternLab.ts` (`resolveConfirmWindows`, `LEVELS_CONFIRM_PATTERN_IDS`), `patternValidation.ts`. Карта иконок: `PatternIcon.tsx` по API `icon`, не по id паттерна.
- Включается в группе **Уровни** (не **Пробой**). Видимое имя — API `label` («Поддержка с трекером»); EN `label_en` («Support Reversal (tracker veto)») в tooltip и под заголовком модалки. Иконка `support_tracker` (линия поддержки + трекер в зоне, без стрелки пробоя) тоже из API и должна отличаться от `support_breakout` и `breakout_up`.
- Этот чип **заменяет** `levels_reversal` / `levels_sr_breakout` для этой стратегии. Изолированный прогон: включить `levels_sr_support` и опционально `signal_4h_buy` / SignalEngine; `levels_reversal`, `levels_sr_breakout` и `level_breakout_retest` оставить выключенными. Если одновременно включён `levels_sr_breakout` — на backend побеждает композит. Если одновременно `levels_sr_support` и `levels_reversal` — побеждает новый id (один support-путь) — это не третий AND.
- Клик по подписи чипа открывает настройки (включает паттерн и подставляет дефолты схемы). Чекбокс переключает; включение параметризованного чипа тоже открывает модалку. Шестерёнка по-прежнему открывает настройки.
- Не хардкодить список параметров во frontend. Схема = только поля `levels_reversal`, без ключей ретеста. Значения вне диапазона — красный бордер и сообщение; «Применить» и «Сохранить и запустить» блокируются. «Сбросить дефолты» возвращает `schema.default`. «Отмена» / Esc отменяет draft.
- Верхнеуровневый `config.confirm_windows` берётся из включённой схемы, у которой есть этот param; приоритет композит > поддержка-с-трекером > `levels_reversal` (как backend `_LEVELS_CONFIRM_PATTERN_IDS`). Сохранение идёт через существующие `POST /api/strategies` и `POST /api/strategies/{id}/run` с `config.patterns` как `{ id: params }` — не `POST /api/backtest`.
- Когда включать: нужен путь B-support из #124 (вето с трекером, без ретеста сопротивления). На locked `test_20260731` оставлять выключенным (строка Lab только для чтения).
- Сервиса `frontend` в `docker-compose.yml` нет. Проверка локально: `cd frontend && npm test && npm run build`. Схема backend: `cd backend && python -m pytest -q tests/test_pattern_registry.py`.
- Isolated vs #124 B-support — задача #129 (handover §31). Портфель 50k — задача #130 (handover §32). Не считать этот чип вердиктом для paper.

## 31. Эксплуатация isolated-вселенной поддержки с трекером

- Пакет: `analytics/issue-129-sr-support-universe/`. Изолированные 28 тикеров Lab (`get_big_tickers`), тот же период, что #124. Не live top-5 и не `run_params.tickers`.
- C = только `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Движок `run_strategy_backtest`, на сделках есть `source=levels_sr_support`.
- Exclusive B-support в #124 — **подпись композита** (путь B забирает dual-бар и занимает единственный слот). Isolated C — **runnable** книга только поддержки: n=4380 PF 1.45. Не считать exclusive 3811 / 1.51 bit-for-bit совпадением с C.
- Extra 611 vs exclusive: 610 occupancy (C входит, пока композит в сделке пути B), leftover 1 (PHOR `2026-08-14 14:48`). Missing 42: cascade (extra C занимает слот, поздняя B-support не стреляет). Extra PF 0.95 — isolated extras хуже exclusive 1.51.
- AFKS: C 89 / 1.49; exclusive 78 ⊆ C; не mix 116 / 1.46. Бар ALRS `2026-08-20 11:50:24` @ 19.80 заблокирован. Resistance-source n=0.
- Задача #130 должна брать C (4380 / 1.45), не exclusive 3811 / 1.51. Не смешивать isolated PF с портфелем 50k. Портфельный пакет: handover §32.
- Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`.
- Повтор без нового бэктеста: `python analytics/issue-129-sr-support-universe/analysis.py`. Полный прогон: `python analytics/issue-129-sr-support-universe/extract_inputs.py` (резюмируется).
- Unit: `cd backend && python -m pytest -q tests/test_issue129_analysis.py`.

## 32. Эксплуатация портфеля поддержки с трекером

- Пакет: `analytics/issue-130-sr-support-portfolio/`. Replay слотов isolated C из #129, те же 28 имён / volume-order, что #103/#44. Не live top-5.
- C = только `levels_sr_support` + `signal_4h_buy` (SHA `3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`). Кандидаты из published #129 `results.json` через `run_strategy_backtest` (`source=levels_sr_support`). Не фильтровать #124 B-mix по `source`.
- Слоты: 50 000 RUB / 10 000 / max 5. Период `2024-08-01` … `timestamp < 2026-08-21`. Дневная equity — по закрытиям, без mark-to-market.
- Опубликованная книга C: n=3237 PF 1.33 equity 96 204.63 daily Max DD 6.08% event Max DD 6.98% skipped 1143, без GAME OVER. Isolated C остаётся 4380 / 1.45 — эти PF не смешивать.
- Сравнение (другие книги, не замены): #44 equity 96 343.49 n=3500 PF 1.31; #103 equity 89 055.31 n=2070 PF 1.34; #124 B-mix equity 98 432.94 n=2837 PF 1.32 (кандидаты support+resistance).
- Бар ALRS `2026-08-20 11:50:24` @ 19.80 отсутствует среди candidates и портфельных входов. Resistance-source n=0.
- Вердикт: не paper (по умолчанию без явного решения PO). Не lock/paper-flag и не overwrite `test_20260731`, `test_20260820`, `test_20260821`. Черновик Lab при необходимости: `test_YYYYMMDD_sr_support`.
- Повтор без нового бэктеста: `python analytics/issue-130-sr-support-portfolio/analysis.py`. JSON слотов: `python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source 129`. Notebook: `python analytics/issue-130-sr-support-portfolio/build_notebook.py --execute`.
- Unit: `cd backend && python -m pytest -q tests/test_issue130_analysis.py`.

## 33. Эксплуатация sandbox LiveExecutor на `test_20260830_new_level` (задача #135)

Явное решение PO поверх вердикта #130 «не paper» для **другой** Lab-строки: `test_20260830_new_level` (`levels_sr_support` + `signal_4h_buy`, RR 1:3). Это не published C (RR 1:2) и не задача #77 (`test_20260731` + top-5 из #66).

1. Ровно одна locked paper-стратегия: `test_20260830_new_level`. `test_20260731` оставить разблокированной; её конфиг не перезаписывать. Открытая paper-позиция FEES на старом имени остаётся под монитор.
2. Пересобрать: `docker compose up -d --build backend`. `/health` должен быть `ok`.
3. Paper-стек должен покрывать понедельничную сессию с запасом. Не останавливать paper ради live. Streaming/refresh покрывает top-15 ∪ LIVE_UNIVERSE (21 имя). `trading.trading_universe` не сужать.
4. Preflight в сессию MOEX (в воскресенье стаканы будут stale):
   `docker compose exec -T backend python -m app.analytics.live_executor_preflight`.
   Проверка падает, если backend нездоров, `LIVE_UNIVERSE` не равен девяти PO-именам, locked-стратегия не одна и не `test_20260830_new_level`, нет свободных sandbox RUB, хотя бы один из девяти стаканов старше пяти минут, paper-процессы запущены не в единственном экземпляре, во вселенной БД не 15 строк или `allow_real_trading` не равен false.
5. Overnight sandbox-день (задача #137), после пересборки backend:
   `START_LIVE_EXECUTOR=1 ./start_processes.sh`
   Не задавать `DURATION_MINUTES`. Запуск в воскресенье вечером; LiveExecutor ждёт понедельника 10:00 МСК. Если paper уже жив и покрывает понедельник 19:00: `START_LIVE_EXECUTOR=1 PRESERVE_PAPER_PROCESSES=1 ./start_processes.sh`.
6. Leftover canary RUAL уже `closed_stop`. На старте #135 открытых sandbox-позиций нет.
7. После окна записать в задачи #135 / #137: init-тикеры, числа `reason=`, число BUY, последние `live_positions` и подтверждение, что `paper_equity` писалась. Никогда не включать `allow_real_trading`.
   Исторический canary с фиксированным окном по-прежнему `DURATION_MINUTES=60` (handover §19).

