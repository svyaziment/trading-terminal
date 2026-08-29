# Smoke AFKS: `levels_sr_breakout` vs #44 / #103

Опубликованный результат [Issue #119](https://github.com/svyaziment/trading-terminal/issues/119)
в эпике [Issue #115](https://github.com/svyaziment/trading-terminal/issues/115).

Изолированный бэктест **одного тикера AFKS**. Это не портфель 50k и не вердикт
катить в paper.

## Цель

Сравнить базу после вето #97 (`levels_reversal` + `signal_4h_buy`, как #103)
с кандидатом (`levels_sr_breakout` + `signal_4h_buy`) на AFKS за то же окно:
`2024-08-01` … `timestamp < 2026-08-21`.

## Методика

- движок A/B: `run_strategy_backtest` (на сделках есть `source`);
- опциональный третий прогон: `run_portfolio_backtest` (plugin-путь Lab после #116);
- `n_runs=1`, без walk-forward и без слотов 10k;
- `level_breakout_retest` выключен;
- `test_20260731`, `test_20260820`, `test_20260821` не lock/paper-flag и не overwrite.

## Состав

| Файл | Назначение |
|---|---|
| `extract_inputs.py` | Снимок `test_20260821` + прогоны A/B/(plugin) |
| `analysis.py` | Метрики, `source`, вердикт, графики |
| `inputs.json` | Конфиги и SHA без секретов |
| `results.json` | Сделки и метрики прогонов |
| `summary.json` | Машиночитаемый итог |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | n/PF, накопленный net %, разбивка source |

## Запуск

Из корня репозитория, если PostgreSQL доступен:

```bash
python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py
python analytics/issue-119-afks-sr-breakout-smoke/analysis.py
```

Только снимок конфигов:

```bash
python analytics/issue-119-afks-sr-breakout-smoke/extract_inputs.py --snapshot-only
```

Повтор отчёта по уже сохранённому `results.json` не требует БД:

```bash
python analytics/issue-119-afks-sr-breakout-smoke/analysis.py
```

Прогон не пишет в `trading.strategies` и не перезаписывает `trading.backtest_results`.
