# Issue #100: бэктест `test_20260820` после вето зоны сопротивления

Опубликованный результат [Issue #100](https://github.com/svyaziment/trading-terminal/issues/100)
после гарда `overlapping_resistance_zone_at` из [Issue #97](https://github.com/svyaziment/trading-terminal/issues/97) /
[PR #98](https://github.com/svyaziment/trading-terminal/pull/98).

Путь: `analytics/issue-100-test-20260820-resistance-veto/`.

Это пакет **Lab full-sample + walk-forward** (`run_strategy_backtest` / `run_walkforward`),
не портфельный прогон из `analytics/issue-100-test-20260820-portfolio/`.

## Цель

Прогнать **только** Lab-стратегию `test_20260820` (id=102, swing-only) на текущем
`StrategyEvaluator` по полной Lab-вселенной (`get_big_tickers`, ≥250k 1min)
и положить воспроизводимый отчёт. Это не пересчёт locked `test_20260731`.
Черновик Lab `run_params.tickers=['ALRS']` не является границей прогона.

## Состав

| Файл | Назначение |
|---|---|
| `extract_inputs.py` | Снимок конфига из БД + full-sample/walk-forward на полной вселенной |
| `analysis.py` | Метрики, сравнение с express Lab id=271, графики, вердикт |
| `inputs.json` | Срез стратегии и baseline без секретов |
| `results.json` | Потикерные метрики и компактный trade list |
| `summary.json` | Машиночитаемые итоги |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | PF по тикерам, equity, walk-forward |

## Запуск

Из корня репозитория, если PostgreSQL доступен:

```bash
python analytics/issue-100-test-20260820-resistance-veto/extract_inputs.py
python analytics/issue-100-test-20260820-resistance-veto/analysis.py
```

Только снимок стратегии (без бэктеста):

```bash
python analytics/issue-100-test-20260820-resistance-veto/extract_inputs.py --snapshot-only
```

Повтор отчёта по уже сохранённому `results.json` не требует БД:

```bash
python analytics/issue-100-test-20260820-resistance-veto/analysis.py
```

Прогон не пишет в `trading.strategies` и не перезаписывает `trading.backtest_results`.
