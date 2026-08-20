# Руководство по передаче контекста агента: Trading Terminal

Последнее обновление: 2026-08-20 (задача #97 вето зоны сопротивления ALRS; синхронизировано с английской версией). Сопутствующий файл: `project-context.ru.md` (английский оригинал: `project-context.md`).
Этот файл — операционное руководство для агентов. Сначала прочитайте `project-context.ru.md` / `project-context.md`, чтобы понять архитектуру.

## 1. Назначение

Операционные знания для безопасной работы с проектом: структура, схема БД, pipeline, API, известные проблемы, roadmap, операционные тонкости и протокол сотрудничества (сбор контекста перед multi-element задачами).

## 2. Структура проекта

Полное дерево см. в разделе 2 `project-context.ru.md`. Ключевые операционные точки входа:
- `backend/app/main.py` - приложение FastAPI + регистрация маршрутов.
- `backend/app/analytics/trading_config.py` - торговая вселенная, live top-5 и реестр стратегий (единый источник истины).
- `start_processes.sh` / `stop_processes.sh` - процессы paper trading и опционального sandbox-исполнения.
- `docs/refresh/context_collector.py` - сборщик контекста для задач агента.

## 3. Схема базы данных

См. раздел 3 `project-context.ru.md`. Новые таблицы включают `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `live_positions`, `trading_universe` и `alerts`. Все находятся в схеме `trading`.

## 4. Конвейер данных

См. раздел 4 `project-context.ru.md`. По умолчанию запускаются четыре paper-процесса:
1. `data_refresher` - MOEX 1min + агрегация + индикаторы + сигналы (каждые 15 минут, top-15).
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
- **Docker rebuild**: после изменений backend-кода ОБЯЗАТЕЛЬНО пересоберите (`docker compose up -d --build backend`).
- **JSON NaN**: pandas создаёт NaN/NaT, которые `json.dumps` отклоняет ("Out of range float values"). Санируйте API-ответы (см. `_json_safe` в `strategy_jobs.py` / `paper_trading_jobs.py`) и приводите timestamp к тексту в SQL (`created_at::text`).
- **JSONB as string**: DBManager возвращает JSONB-колонки как Python-repr строки, а не dict. Нормализуйте через `_to_dict` (json.loads, затем fallback ast.literal_eval).
- **Backtest matrix runtime**: полная матрица занимает ~10-15 мин. Для liveness используйте quick=true.
- **Reports mount**: backend монтирует `./reports` (docker-compose). Прогоны стратегий пишут `reports/strategy-lab/last_run.json` - отправляйте его при любой ошибке Strategy Lab.
- **Вето зоны сопротивления (задача #97)**: `levels_reversal` не должен входить, если 1min close лежит в активной зоне сопротивления, даже если `nearest_level_at(..., 'support')` вернул валидную поддержку и расширение 0.5×ATR покрывает fill. Это структурный дефект, а не role-reversal (ALRS paper #711: fill 19.80 внутри импульсного сопротивления 19.67). Гард: `overlapping_resistance_zone_at` в `StrategyEvaluator.check_entry`. Locked `test_20260731` не перезаписывать. Unit: `cd backend && python -m pytest -q tests/test_resistance_zone_veto.py`.

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
- Безопасный запуск: `START_LIVE_EXECUTOR=1 ./start_processes.sh`. Дополнительный opt-in не даёт обычному paper workflow выставлять sandbox-ордера. Лог: `reports/live-executor/executor.log`.
- Порядок обработки фиксирован: BUY от `StrategyEvaluator` -> свежий imbalance -> свободные RUB -> position sizing -> market BUY -> take sell-limit -> запись/сверка БД.
- Stop-защита синтетическая. Нельзя выставлять stop sell-limit при входе: limit ниже рынка исполнился бы сразу. Монитор ждёт `current_price <= stop_price`, отменяет take и затем выставляет sell-limit по наблюдаемой цене.
- Каждая физическая попытка broker API, включая retry SDK и обнаружение счёта, использует один token bucket (`api_rate_limit`, максимум 10/сек). Не добавляйте вызовы в обход `_broker_call` или клиентского hook `before_request`.
- SIGTERM/SIGINT запрашивает очистку. Ожидающие entry/protection заявки отменяются; открытые позиции закрываются только при `close_positions_on_shutdown=true`. При значении false по умолчанию позиции остаются открытыми, а их protection IDs очищаются в БД.
- Причины отказа BUY читайте в `reports/live-executor/executor.log`. Каждая запись `Live signal skipped` содержит `ticker=<тикер>`, стабильный `reason=<код>` и релевантные значения. Ожидаемые коды фильтров и лимитов: `stale_or_missing_orderbook`, `imbalance_below_threshold`, `insufficient_cash`, `invalid_stop`, `insufficient_capital`, `max_open_positions` и `broker_error`; `min_lot` по контракту sizing остаётся исполнимым. Например, `reason=imbalance_below_threshold imbalance=0.9 imbalance_threshold=1.0` означает, что стрим работает, но фильтр отклонил вход, а `reason=stale_or_missing_orderbook orderbook_age_seconds=missing` — что данных стакана нет. Эти записи появляются только после BUY-решения от `StrategyEvaluator`; отсутствие записей об отказах может означать, что BUY-сигналов не было. Для broker errors логируются только операция и тип исключения, без реквизитов счёта, credentials и текста исключения.
- Диагностика: `SELECT * FROM trading.live_positions WHERE status IN ('pending','open') ORDER BY id;`.
- Тесты: `cd backend && python -m pytest -q tests/test_live_executor.py`.

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

- Paper и data refresh оставляют `get_trading_universe()` (top-15 из `trading.trading_universe`).
- Sandbox-исполнение использует `LIVE_UNIVERSE` / `get_live_trading_universe()`: SBER, LKOH, RUAL, NVTK, GAZP (задача #66). `LiveExecutor.initialize()` пересекает тикеры paper-стратегии с этим списком.
- Не сужайте таблицу БД до пяти имён: это остановит стриминг и paper trading по остальному top-15.
- Код рейтинга и отчёт: `analytics/issue-66-live-universe/`. После нового снимка `extract_inputs.py` перезапустите `analysis.py` и синхронизируйте `LIVE_UNIVERSE` с `summary.json`.
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
