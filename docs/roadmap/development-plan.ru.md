# Trading Terminal - План развития

Сформировано: 2026-07-23
Сформировано задачей: task-037-roadmap-development-plan
Назначение: дорожная карта развития проекта для человека
Исходный контекст: docs/agents/project-context.md

Этот документ описывает развитие проекта крупными блоками.
Он включает backtest-инструменты, подключение CatBoost и LightGBM, обучение, переобучение и прогнозирование.

---

## 1. Текущее состояние

Проект:
- аналитический терминал для MOEX;
- интеграция с T-Bank / Tinkoff Invest API;
- режим: sandbox / разработка;
- production-торговля отключена.

Подтверждённое состояние:
- ветка: main;
- backend на FastAPI существует;
- PostgreSQL схема trading существует;
- pipeline аналитики существует:
  - инструменты;
  - 30-минутные свечи;
  - агрегированные свечи;
  - индикаторы;
  - сигналы;
  - ТОП-акции по объёму;
- сгенерировано 42275 сигналов;
- сигналы есть для 30min, 1h, 4h;
- 1d сигналов пока нет;
- 1d индикаторов мало: 274 строки;
- frontend исходники есть;
- npm зависимости устанавливались успешно;
- production build frontend ещё не проверен;
- расширенный signals API проверен не полностью.

Важно:
- использовать импорты app.*, а не src.*;
- signal engine находится в backend/app/analytics/signal_engine.py;
- base паттернов находится в backend/app/analytics/patterns/base.py;
- backend/app/api/signals.py похож на legacy и требует проверки;
- все задачи должны создавать отчёты в reports/<task_id>/.

---

## 2. Общие принципы

1. Только sandbox.
   - Никакой production-торговли.
   - Никаких production-токенов в тестах.
   - Реальные заявки только после отдельного риск-блока и явного разрешения.

2. Разработка через задачи.
   - Каждое изменение делается через task-скрипт.
   - Каждая задача создаёт report.json.
   - Оркестратор читает report.json перед следующим шагом.

3. Идемпотентность.
   - Загрузку данных, агрегацию, индикаторы, сигналы, фичи, backtest и прогнозы можно безопасно повторять.
   - Лучше использовать удаление диапазона и вставку или upsert.

4. Запрет утечки будущих данных.
   - ML-фичи и лейблы используют только прошлые данные.
   - Разделение данных только по времени.
   - Между train/validation/test нужен purge или embargo.

5. Наблюдаемость.
   - Важные операции имеют логи, метрики и артефакты.
   - Обучение моделей сохраняет параметры, метрики, окно данных, git commit и ссылку на модель.

6. Безопасность.
   - Не удалять аналитические таблицы без backup.
   - Не менять trading.signals вручную.
   - Не коммитить .env, токены, пароли, сертификаты.

---

## 3. Крупные блоки развития

Блок A: Стабилизация и готовность API
Блок B: Качество данных и feature store
Блок C: Backtesting engine
Блок D: ML-фичи и лейблы
Блок E: Обучение CatBoost и LightGBM
Блок F: Сервис прогнозов и интеграция
Блок G: Переобучение и мониторинг
Блок H: Риск, заявки и схема terminal
Блок I: Наблюдаемость и управление

Рекомендуемая последовательность:
A -> B -> C -> D -> E -> F -> G -> H -> I

Можно частично параллелить:
- Block C можно начинать после Block A и части Block B.
- Block D можно начинать после Block B.
- Block H можно проектировать раньше, но реализовывать после Block F.

---

## 4. Блок A - Стабилизация и готовность API

Цель:
Сделать текущий backend, frontend и API стабильными и проверенными.

Что входит:
- проверить npm run build;
- проверить frontend dev server;
- проверить backend /health;
- проверить текущие API endpoints;
- проверить, что /api/signals поддерживает:
  - пагинацию;
  - фильтр по дате;
  - сортировку;
  - pattern_name;
  - figi;
  - статистику;
- при необходимости доработать API;
- диагностировать отсутствие 1d сигналов;
- закоммитить документацию и скрипты.

Примеры задач:
- task-038-frontend-build-verify
- task-039-backend-api-verify
- task-040-signals-api-enhance
- task-041-daily-signals-diagnostics
- task-042-commit-docs-scripts

Критерии готовности:
- npm run build проходит;
- frontend запускается;
- backend отвечает /health;
- /api/signals возвращает pattern_name и figi;
- /api/signals поддерживает limit, offset, date_from, date_to, sort_by, sort_dir;
- есть /api/signals/stats или явно зафиксировано, что его ещё нет;
- причина отсутствия 1d сигналов понятна.

Риски:
- frontend ждёт поля, которых нет в API;
- ручные изменения не закоммичены;
- для 1d недостаточно истории.

---

## 5. Блок B - Качество данных и feature store

Цель:
Подготовить надёжный слой данных для backtest и ML.

Что входит:
- проверка инструментов;
- проверка 30-минутных свечей;
- проверка агрегированных свечей;
- проверка индикаторов;
- проверки качества данных:
  - пропуски свечей;
  - дубликаты;
  - null OHLCV;
  - нулевые объёмы;
  - некорректные timestamp;
  - расхождения ticker/figi;
  - разрывы таймфреймов;
- создание схемы для ML;
- хранение нормализованных фичей;
- версионирование feature set.

Примеры объектов БД:
- schema ml;
- ml.features;
- ml.feature_sets;
- ml.data_quality_checks;
- ml.data_sources.

Примеры фичей:
- lag returns: 1, 2, 3, 5, 10, 20 баров;
- rolling volatility;
- volume ratio;
- RSI, MACD, ATR, Bollinger;
- отношения SMA/EMA;
- тело свечи и тени;
- hour, weekday, month;
- ticker как категориальная фича;
- timeframe как категориальная фича;
- ликвидность или rank из top_stocks_by_volume.

Критерии готовности:
- расчёт фичей детерминирован;
- фичи хранятся с ticker, timeframe, timestamp, feature_set, version;
- нет утечки будущих данных;
- есть отчёт о качестве данных.

Риски:
- leakage;
- дубликаты фичей;
- несогласованные таймзоны;
- разные версии индикаторов.

---

## 6. Блок C - Backtesting engine

Цель:
Создать движок backtest для проверки правил и ML-прогнозов.

Что входит:
- исторический прогон по candles_aggregated;
- поддержка universe:
  - top_stocks_by_volume;
  - ручной список;
- источники сигналов:
  - trading.signals;
  - ml.predictions;
  - будущие стратегии;
- симуляция входов и выходов;
- сначала long-only;
- позже можно short;
- комиссия;
- slippage;
- spread approximation;
- учёт lot_size;
- учёт min_price_increment;
- equity curve;
- сделки;
- метрики.

Примеры объектов БД:
- ml.backtest_runs;
- ml.backtest_trades;
- ml.backtest_positions;
- ml.backtest_equity;
- ml.backtest_metrics.

Метрики:
- total return;
- CAGR;
- Sharpe;
- Sortino;
- max drawdown;
- win rate;
- profit factor;
- average trade return;
- exposure;
- number of trades;
- turnover;
- cost impact.

Критерии готовности:
- backtest детерминирован;
- backtest не использует будущие свечи;
- backtest сохраняет параметры и git commit;
- можно сравнить паттерны и ML;
- результаты воспроизводимы.

Риски:
- look-ahead bias;
- нереалистичные исполнения;
- игнорирование лотов;
- игнорирование торгового календаря;
- переобучение под историю.

---

## 7. Блок D - ML-фичи и лейблы

Цель:
Подготовить ML-датасеты с правильными целевыми переменными.

Что входит:
- горизонты:
  - 1h;
  - 4h;
  - 1d;
- лейблы:
  - направление будущего движения;
  - доходность выше порога с учётом затрат;
  - позже triple-barrier;
- классы:
  - BUY;
  - SELL;
  - NO_TRADE;
- датасеты;
- train/validation/test;
- purge и embargo;
- версионирование датасетов.

Примеры объектов БД:
- ml.labels;
- ml.datasets;
- ml.dataset_splits;
- ml.label_definitions.

Примеры лейблов:
- next_1h_return;
- next_4h_return;
- next_1d_return;
- next_1h_return_after_costs;
- next_1d_direction;
- next_1d_class.

Критерии готовности:
- лейблы используют только будущую цену относительно timestamp фичей;
- фичи и лейблы соединяются по ticker, timeframe, timestamp;
- split по времени;
- test не попадает в train;
- версия датасета сохраняется.

Риски:
- label leakage;
- дисбаланс классов;
- мало положительных примеров;
- несоответствие горизонта фичей и лейбла.

---

## 8. Блок E - Обучение CatBoost и LightGBM

Цель:
Добавить обучение моделей CatBoost и LightGBM.

Что входит:
- ML-зависимости:
  - catboost;
  - lightgbm;
  - scikit-learn;
  - shap;
  - joblib;
- скрипты обучения;
- CatBoostClassifier;
- LGBMClassifier;
- категориальные фичи:
  - ticker;
  - timeframe;
  - hour;
  - weekday;
  - liquidity bucket;
- эксперименты;
- артефакты моделей;
- метрики;
- hyperparameter search;
- champion/challenger.

Примеры объектов БД:
- ml.experiments;
- ml.models;
- ml.model_metrics;
- ml.model_artifacts.

Метаданные модели:
- model_id;
- model_type;
- feature_set_version;
- dataset_version;
- label_definition;
- horizon;
- params;
- metrics;
- git_commit;
- created_at;
- status: candidate, champion, archived.

Метрики:
- accuracy;
- precision;
- recall;
- f1;
- roc_auc;
- pr_auc;
- logloss;
- calibration error;
- top quantile precision;
- feature importance.

Критерии готовности:
- обучение воспроизводимо;
- модель можно загрузить;
- модель умеет predict;
- метрики сохранены по split;
- champion выбирается по validation и backtest;
- CatBoost и LightGBM можно сравнить.

Риски:
- переобучение;
- leakage;
- большой размер модели;
- неправильный порядок фичей;
- неправильная работа с категориальными фичами.

---

## 9. Блок F - Сервис прогнозов и интеграция

Цель:
Использовать обученные модели для прогнозов и показать их в терминале.

Что входит:
- batch prediction pipeline;
- прогнозы для top stocks или universe;
- хранение прогнозов в ml.predictions;
- API:
  - GET /api/ml/models;
  - GET /api/ml/predictions;
  - POST /api/ml/predict;
  - GET /api/backtests;
  - GET /api/backtests/<id>/metrics;
- frontend страницы:
  - Models;
  - Predictions;
  - Backtests;
  - Model detail;
- сравнение ML-прогнозов и rule-based сигналов.

Примеры объектов БД:
- ml.predictions;
- ml.prediction_runs.

Поля прогноза:
- ticker;
- figi;
- timeframe;
- timestamp;
- model_id;
- horizon;
- predicted_class;
- probability_buy;
- probability_sell;
- probability_no_trade;
- expected_return;
- confidence;
- features_version;
- created_at.

Критерии готовности:
- прогнозы детерминированы для фиксированной модели и snapshot фичей;
- прогнозы не используют будущие данные;
- API возвращает метаданные модели;
- frontend показывает прогнозы и метрики;
- ML-прогнозы можно прогнать через backtest.

Риски:
- устаревшие фичи;
- несовпадение версии модели;
- drift схемы фичей;
- попадание прогнозов в обучение.

---

## 10. Блок G - Переобучение и мониторинг

Цель:
Обеспечить безопасное переобучение и контроль качества моделей.

Что входит:
- расписание переобучения:
  - daily;
  - weekly;
  - rolling window;
- champion/challenger;
- validation gates;
- promotion только при прохождении порогов;
- data drift;
- prediction drift;
- performance decay;
- расхождение backtest и live.

Примеры объектов БД:
- ml.monitoring_metrics;
- ml.retraining_jobs;
- ml.model_promotions.

Метрики мониторинга:
- drift распределения фичей;
- drift распределения прогнозов;
- drift частоты лейблов;
- падение precision;
- падение roc_auc;
- расхождение backtest/live;
- свежесть данных.

Критерии готовности:
- переобучение создаёт нового кандидата;
- кандидат проверяется перед promotion;
- старый champion можно восстановить;
- есть мониторинг;
- есть алерты на критический drift.

Риски:
- автоматический promotion переобученной модели;
- concept drift;
- ухудшение качества данных;
- обучение на битых данных.

---

## 11. Блок H - Риск, заявки и схема terminal

Цель:
Подготовить операционную часть терминала с жёстким контролем риска.

Что входит:
- схема terminal;
- сущности:
  - accounts;
  - broker_connections;
  - orders;
  - order_executions;
  - positions;
  - portfolio_snapshots;
  - risk_limits;
  - risk_checks;
  - trading_controls;
  - audit_logs;
- order preview;
- risk checks;
- human confirmation;
- real execution по умолчанию выключен;
- kill switch.

Риск-лимиты:
- max order amount;
- max daily loss;
- max open orders;
- max orders per minute;
- max position size;
- instrument blacklist;
- market orders disabled by default;
- margin trading disabled by default;
- trading enabled flag.

Критерии готовности:
- заявка не может обойти risk checks;
- каждое действие пишется в audit;
- нужно подтверждение человека;
- kill switch останавливает новые заявки;
- production trading выключен до явного разрешения.

Риски:
- случайная реальная торговля;
- отсутствие audit trail;
- неправильные лимиты;
- необработанные ошибки брокера.

---

## 12. Блок I - Наблюдаемость и управление

Цель:
Сделать систему наблюдаемой, аудируемой и сопровождаемой.

Что входит:
- структурированные логи;
- metrics endpoint;
- Prometheus;
- Grafana;
- алерты;
- dashboard по task-отчётам;
- dashboard по моделям;
- обновление документации;
- security review;
- backup и restore.

Возможные метрики:
- backend health;
- DB latency;
- свежесть данных;
- длительность генерации сигналов;
- длительность обучения;
- latency прогнозов;
- длительность backtest;
- error rate;
- drift metrics.

Критерии готовности:
- критические ошибки видны;
- свежесть данных контролируется;
- метрики моделей видны;
- отчёты легко найти;
- секреты не попадают в логи.

Риски:
- слишком много метрик;
- отсутствие алертов;
- чувствительные данные в логах.

---

## 13. Рекомендуемая последовательность задач

Фаза 1: Стабилизация
- task-038-frontend-build-verify
- task-039-backend-api-verify
- task-040-signals-api-enhance
- task-041-daily-signals-diagnostics
- task-042-commit-docs-scripts

Фаза 2: Данные и фичи
- task-043-data-quality-checks
- task-044-ml-schema
- task-045-feature-store
- task-046-feature-validation

Фаза 3: Backtesting
- task-047-backtest-engine-core
- task-048-backtest-metrics
- task-049-backtest-api
- task-050-backtest-frontend

Фаза 4: ML
- task-051-label-framework
- task-052-dataset-splitter
- task-053-catboost-trainer
- task-054-lightgbm-trainer
- task-055-model-registry

Фаза 5: Прогнозы и переобучение
- task-056-prediction-pipeline
- task-057-prediction-api
- task-058-retraining-pipeline
- task-059-monitoring-metrics

Фаза 6: Terminal и риск
- task-060-terminal-schema
- task-061-risk-limits
- task-062-order-preview
- task-063-audit-logs

---

## 14. Краткая сводка

Версия плана: 1
Проект: trading-terminal
Режим: sandbox
Production trading: false
Текущая ветка: main
Аналитическая схема: trading
Будущие схемы: ml, terminal
Следующий приоритет: Block A - стабилизация и готовность API
