# Lab-вселенная: `levels_sr_support` vs #124 B-support

Опубликованный результат [Issue #129](https://github.com/svyaziment/trading-terminal/issues/129)
в эпике [Issue #126](https://github.com/svyaziment/trading-terminal/issues/126).

Изолированный бэктест **полной Lab-вселенной** (`get_big_tickers`, 28 имён).
Это не портфель 50k и не вердикт катить в paper.

## Цель

Колонка **B-support** в #124 — exclusive-подпись композита (путь B занимает слот).
Isolated C — runnable support-only: книга B-support должна входить в C, extra
объясняются occupancy. Итог: C n=4380 PF 1.45; exclusive 3811 / 1.51 **не**
bit-for-bit. AFKS: C n=89 PF 1.49, exclusive 78 ⊆ C, extra=11 occupancy.

## Методика

- вселенная: `get_big_tickers(min_candles=250000)`, не `run_params.tickers` и не live top-5;
- движок C: `run_strategy_backtest` (на сделках есть `source=levels_sr_support`);
- период `2024-08-01` … `timestamp < 2026-08-21`;
- `n_runs=1`, без walk-forward и без слотов 10k;
- `levels_reversal`, `levels_sr_breakout`, `level_breakout_retest` выключены;
- `test_20260731`, `test_20260820`, `test_20260821` не lock/paper-flag и не overwrite;
- целевая книга: `analytics/issue-124-sr-breakout-universe/` (колонка B support).

## Состав

| Файл | Назначение |
|---|---|
| `extract_inputs.py` | Снимок `test_20260821` + прогон C по вселенной |
| `analysis.py` | Метрики, регрессия vs #124 B-support, AFKS, ALRS, вердикт, графики |
| `inputs.json` | Конфиг C и SHA без секретов |
| `results.json` | Сделки и метрики по тикерам |
| `summary.json` | Машиночитаемый итог |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | n/PF vs #124, n и PF по тикерам |

## Запуск

Из корня репозитория, если PostgreSQL доступен:

```bash
python analytics/issue-129-sr-support-universe/extract_inputs.py --workers 4
python analytics/issue-129-sr-support-universe/analysis.py
```

Только снимок конфига:

```bash
python analytics/issue-129-sr-support-universe/extract_inputs.py --snapshot-only
```

Прогон резюмируется: уже успешные `C/ticker` в `results.json` пропускаются.

Повтор отчёта по уже сохранённому `results.json` не требует БД:

```bash
python analytics/issue-129-sr-support-universe/analysis.py
```

Прогон не пишет в `trading.strategies` и не перезаписывает `trading.backtest_results`.
Jupyter / портфель 50k — задача #130.
