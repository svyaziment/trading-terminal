# Портфельный бэктест test_20260821

Опубликованный результат [Issue #103](https://github.com/svyaziment/trading-terminal/issues/103).
Методика скопирована с [Issue #44](https://github.com/svyaziment/trading-terminal/issues/44) /
`analytics/issue-44-strategy-comparison/`: один конфиг, один портфель.

## Цель

Прогнать Lab-конфиг `test_20260821` (`trading.strategies.id=118`) через
`run_portfolio_backtest` + replay общего капитала. Это не таблица Lab
full-sample/walk-forward и не сравнение с ATR.

Конфиг читается из БД (`normalize_patterns`). Locked `test_20260731` (id=36)
и swing-only `test_20260820` (id=102) не трогаются.

Отличие от `test_20260820`: снова включён **impulse**, как у locked
`test_20260731`, но на движке **после** вето #97.

## Методика

- период: 2024-08-01 … 2026-08-20 (запрос `timestamp < 2026-08-21`, exclusive
  как в `MODE_PRESETS`);
- вселенная: все тикеры периода по `get_tickers_by_volume`, без сужения до
  ALRS / live top-5;
- стартовый капитал: 50 000 RUB;
- размер слота: 10 000 RUB;
- максимум пять одновременных позиций;
- конкуренция за слот — статический рейтинг объёма;
- комиссия включена в `net_return_pct`;
- дневная equity строится по закрытым сделкам без mark-to-market;
- Max DD в отчёте считается по equity на конец дня;
- event-based Max DD симулятора сохраняется отдельно;
- движок — текущий `StrategyEvaluator` с вето resistance-зоны (#97).

Режим `--mode dev` только для liveness, в PR не как основной результат.

## Состав

| Файл | Назначение |
|---|---|
| `analysis.py` | Воспроизводимый расчёт метрик, отчёта и графиков |
| `analysis.ipynb` | Jupyter notebook для проверки (собрать через `build_notebook.py`) |
| `generate_inputs.py` | Параллельная генерация JSON-прогона |
| `build_notebook.py` | Воспроизводимая сборка notebook |
| `summary.json` | Машиночитаемые итоговые метрики |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | Equity, распределение PnL, heatmap тикеров, таблица метрик |

Исходный полный прогон остаётся в локальном артефакте
`reports/Arctic/103_test-20260821-portfolio/` (gitignored).

## Запуск

Из корня репозитория:

```bash
python analytics/issue-103-test-20260821-portfolio/analysis.py
```

Полная повторная генерация входа требует доступ к PostgreSQL:

```bash
python analytics/issue-103-test-20260821-portfolio/generate_inputs.py \
  --mode full --workers 4
```

После изменения структуры notebook:

```bash
python analytics/issue-103-test-20260821-portfolio/build_notebook.py
```
