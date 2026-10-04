# Imbalance стакана в реальном времени

> **Source:** project-context.ru.md sections 11 + handover.ru.md sections 12
> **Last refreshed:** 2026-10-04, task-346

## 11. Imbalance стакана в реальном времени

`backend/app/analytics/orderbook_imbalance.py` — общий калькулятор и обязательный фильтр live-входа для задачи #60. При каждом streaming-обновлении стакана `online_data.py` суммирует объёмы первых 10 уровней bid и ask из конфига и сохраняет:

`volume_imbalance = bid_depth / ask_depth`

Инфраструктурные значения находятся в `trading_config.py` (`ORDERBOOK_IMBALANCE`): глубина 10, максимальный возраст агрегата 5 минут и порог по умолчанию 1.0. Активная стратегия может переопределить только верхнеуровневый `imbalance_threshold`; live-вход проходит, когда конечное значение imbalance строго больше порога.

Перед генерацией каждого сигнала `live_engine.py` заново рассчитывает отношение из `bid_depth` и `ask_depth` последней свежей строки `trading.online_orderbook_aggregates`. Отсутствующая или устаревшая строка, null/нечисловые значения и нулевая ask depth дают `None`, поэтому обязательный фильтр отклоняет сигнал, а не использует ноль или старые данные. Legacy online signal path использует тот же калькулятор.

## 12. Эксплуатация фильтра imbalance стакана

- Точки входа: `online_data.save_orderbook_aggregate` рассчитывает и сохраняет каждое streaming-обновление; `orderbook_imbalance.get_recent_imbalance` читает свежий агрегат; `passes_imbalance_filter` служит обязательным шлюзом сигнала.
- Инфраструктурная политика находится в `ORDERBOOK_IMBALANCE` файла `trading_config.py`: глубина 10, максимальный возраст 5 минут, порог по умолчанию 1.0. Переопределение стратегией: верхнеуровневый `config.imbalance_threshold`.
- Условие прохождения строгое: `volume_imbalance > imbalance_threshold`. Отсутствующие, устаревшие, null, NaN/бесконечные данные и нулевая ask depth всегда отклоняют сигнал.
- Быстрая диагностика БД:
  `SELECT ticker, timestamp, bid_depth, ask_depth, volume_imbalance FROM trading.online_orderbook_aggregates ORDER BY timestamp DESC LIMIT 20;`
- Если все live-сигналы пропускаются, сначала убедитесь, что `online_data` запущен, а последняя строка моложе 5 минут. Не ослабляйте защиту от отсутствующих данных.
- Unit-тест: `cd backend && python -m pytest -q tests/test_orderbook_imbalance.py`.
