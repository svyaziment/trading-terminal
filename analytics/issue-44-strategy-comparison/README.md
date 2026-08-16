# Сравнение стратегий levels_reversal и ATR reversal

Опубликованный результат [Issue #44](https://github.com/svyaziment/trading-terminal/issues/44),
исправленный в рамках [Issue #55](https://github.com/svyaziment/trading-terminal/issues/55).
Входит в эпик [#39](https://github.com/svyaziment/trading-terminal/issues/39).

## Цель

Сравнить две реализации `StrategyPlugin` на одном портфеле за период
с августа 2024 по август 2026:

- `levels_reversal` — базовая стратегия разворота от уровней;
- `atr_reversal` — ATR-разворот по подходу Звездина.

## Методика

- стартовый капитал: 50 000 RUB;
- размер слота: 10 000 RUB;
- максимум пять одновременных позиций;
- конкуренция за слот разрешается по статическому рейтингу объёма;
- комиссия включена в `net_return_pct`;
- дневная equity строится по закрытым сделкам без mark-to-market;
- Max DD в отчёте считается по equity на конец дня;
- event-based Max DD симулятора сохраняется отдельно для аудита.

## Состав

| Файл | Назначение |
|---|---|
| `analysis.py` | Воспроизводимый расчёт метрик, отчёта и графиков |
| `analysis.ipynb` | Исполненный Jupyter notebook для проверки |
| `generate_inputs.py` | Параллельная генерация JSON-прогонов |
| `build_notebook.py` | Воспроизводимая сборка notebook |
| `summary.json` | Машиночитаемые итоговые метрики |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | Четыре итоговые визуализации |

Исходные полные прогоны остаются в локальных артефактах `reports/Arctic/`.

## Запуск

Из корня репозитория:

```bash
python analytics/issue-44-strategy-comparison/analysis.py
```

Полная повторная генерация входов требует доступ к PostgreSQL:

```bash
python analytics/issue-44-strategy-comparison/generate_inputs.py \
  --strategy both --mode full --workers 4
```

После изменения структуры notebook:

```bash
python analytics/issue-44-strategy-comparison/build_notebook.py
```
