# Lab-вселенная: `levels_sr_breakout` vs #103 / #119

Опубликованный результат [Issue #124](https://github.com/svyaziment/trading-terminal/issues/124)
в эпике [Issue #115](https://github.com/svyaziment/trading-terminal/issues/115).

Изолированный бэктест **полной Lab-вселенной** (`get_big_tickers`, 28 имён).
Это не портфель 50k и не вердикт катить в paper.

## Цель

Сравнить базу после вето #97 (`levels_reversal` + `signal_4h_buy`, как #103 / прогон A)
с кандидатом (`levels_sr_breakout` + `signal_4h_buy`) на 28 тикерах за то же окно:
`2024-08-01` … `timestamp < 2026-08-21`. Следующий шаг после вердикта #119
«расширять вселенную».

## Методика

- вселенная: `get_big_tickers(min_candles=250000)`, не `run_params.tickers` и не live top-5;
- движок A/B: `run_strategy_backtest` (на сделках есть `source`);
- конфиги A/B — те же SHA, что в #119;
- `n_runs=1`, без walk-forward;
- `level_breakout_retest` выключен;
- `test_20260731`, `test_20260820`, `test_20260821` не lock/paper-flag и не overwrite;
- опциональный портфельный replay B (50k / 10k слоты, max 5) — отдельный блок, только если isolated B устойчив.

## Состав

| Файл | Назначение |
|---|---|
| `extract_inputs.py` | Снимок `test_20260821` + прогоны A/B по вселенной |
| `analysis.py` | Метрики, `source`, регрессия AFKS, ALRS, вердикт, графики |
| `inputs.json` | Конфиги и SHA без секретов |
| `results.json` | Сделки и метрики по тикерам |
| `summary.json` | Машиночитаемый итог |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | n/PF, PF по тикерам, разбивка source |

## Запуск

Из корня репозитория, если PostgreSQL доступен:

```bash
python analytics/issue-124-sr-breakout-universe/extract_inputs.py --workers 4
python analytics/issue-124-sr-breakout-universe/analysis.py
```

Только снимок конфигов:

```bash
python analytics/issue-124-sr-breakout-universe/extract_inputs.py --snapshot-only
```

Прогон резюмируется: уже успешные `A/ticker` и `B/ticker` в `results.json` пропускаются.

Повтор отчёта по уже сохранённому `results.json` не требует БД, кроме случая,
когда вердикт запускает портфельный replay (он идёт по уже снятым сделкам B):

```bash
python analytics/issue-124-sr-breakout-universe/analysis.py
```

Прогон не пишет в `trading.strategies` и не перезаписывает `trading.backtest_results`.
