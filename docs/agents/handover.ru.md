# Руководство по передаче контекста агента: Trading Terminal

Последнее обновление: 2026-08-17 (задачи #59-#60 Live Trading Infrastructure; синхронизировано с английской версией). Сопутствующий файл: `project-context.ru.md` (английский оригинал: `project-context.md`).
Этот файл — операционное руководство для агентов. Сначала прочитайте `project-context.ru.md` / `project-context.md`, чтобы понять архитектуру.

## 1. Назначение

Операционные знания для безопасной работы с проектом: структура, схема БД, pipeline, API, известные проблемы, roadmap, операционные тонкости и протокол сотрудничества (сбор контекста перед multi-element задачами).

## 2. Структура проекта

Полное дерево см. в разделе 2 `project-context.ru.md`. Ключевые операционные точки входа:
- `backend/app/main.py` - приложение FastAPI + регистрация маршрутов.
- `backend/app/analytics/trading_config.py` - торговая вселенная + реестр стратегий (единый источник истины).
- `start_processes.sh` / `stop_processes.sh` - процессы paper trading.
- `docs/refresh/context_collector.py` - сборщик контекста для задач агента.

## 3. Схема базы данных

См. раздел 3 `project-context.ru.md`. Новые таблицы (Strategy Lab + paper trading): `strategies`, `backtest_results`, `paper_positions`, `paper_equity`, `trading_universe`, `alerts`. Все находятся в схеме `trading`.

## 4. Конвейер данных

См. раздел 4 `project-context.ru.md`. Четыре фоновых процесса (запускаются через `start_processes.sh`):
1. `data_refresher` - MOEX 1min + агрегация + индикаторы + сигналы (каждые 15 минут, top-15).
2. `online_data` - стриминг 1min свечей + стакана.
3. `live_engine` - читает активную стратегию из БД (`paper_strategy.get_active_paper_strategy`), строит 4h контекст через `build_strategy_context`, передаёт живые 1min бары в per-ticker `StrategyEvaluator` (единая логика входа, та же что в бэктесте), генерирует сигналы в `trading.alerts`.
4. `paper_trader` - читает конфиг стратегии из БД (RR из `config.risk_reward`), alerts -> market позиции (open по best_ask, один arm) -> мониторинг stop/take -> запись equity. Записывает `strategy_name` в `paper_positions`.
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
