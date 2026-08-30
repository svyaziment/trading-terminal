# Портфель 50k: `levels_sr_support`

Опубликованный результат [Issue #130](https://github.com/svyaziment/trading-terminal/issues/130)
в эпике [Issue #126](https://github.com/svyaziment/trading-terminal/issues/126).
Методика скопирована с [Issue #44](https://github.com/svyaziment/trading-terminal/issues/44) /
`analytics/issue-44-strategy-comparison/` и [Issue #103](https://github.com/svyaziment/trading-terminal/issues/103).

Это портфельный replay isolated-книги C, не isolated PF и не вердикт катить в paper.

## Цель

Прогнать **`levels_sr_support` + `signal_4h_buy`** (конфиг C из #129, SHA
`3b7864c4de2cb2c7d271be8c21c7d99c29bfd8a7dd05980b3c5497b6b2aedb1b`) через слоты
50k / 10k / max 5. Кандидаты берутся из published `analytics/issue-129-sr-support-universe/`,
не фильтром `source` из композита #124 и не из exclusive-колонки 3811 / 1.51.

Locked `test_20260731`, `test_20260820` и `test_20260821` не трогаются.

## Итог

| Книга | Equity, RUB | n | PF | Max DD | GAME OVER |
|---|---:|---:|---:|---:|:---:|
| #44 `test_20260731` | 96 343.49 | 3500 | 1.31 | 5.47% | нет |
| #103 `test_20260821` | 89 055.31 | 2070 | 1.34 | 6.82% | нет |
| **C `levels_sr_support`** | **96 204.63** | **3237** | **1.33** | **6.08%** | **нет** |
| #124 B-mix | 98 432.94 | 2837 | 1.32 | 7.93%* | нет |

\* у B-mix опубликован event-based Max DD. Кандидаты C: 4380. ALRS 19.80 нет.
Вердикт: **не paper**.

## Методика

- период: 2024-08-01 … 2026-08-20 (запрос `timestamp < 2026-08-21`);
- вселенная: пересечение `get_big_tickers` с volume-order #103/#44 (28 имён);
- стартовый капитал: 50 000 RUB;
- размер слота: 10 000 RUB;
- максимум пять одновременных позиций;
- конкуренция за слот — статический рейтинг объёма;
- комиссия включена в `net_return_pct`;
- дневная equity строится по закрытым сделкам без mark-to-market;
- Max DD в отчёте считается по equity на конец дня;
- event-based Max DD симулятора сохраняется отдельно;
- движок кандидатов — `run_strategy_backtest` из пакета #129 (`source=levels_sr_support`).

## Состав

| Файл | Назначение |
|---|---|
| `generate_inputs.py` | Replay слотов из published C (или `--source db`) |
| `analysis.py` | Метрики, сравнение книг, графики, `report.md` / `summary.json` |
| `build_notebook.py` | Сборка `analysis.ipynb` |
| `analysis.ipynb` | Исполненный notebook |
| `summary.json` | Машиночитаемые итоговые метрики |
| `report.md` | Аналитический отчёт |
| `plots/*.png` | Equity, распределение PnL, heatmap тикеров, таблица книг |

Исходный полный прогон остаётся в локальном артефакте
`reports/Vulpec/130_sr-support-portfolio/` (gitignored).

## Запуск

Из корня репозитория (без нового бэктеста, нужны published #129 `results.json`):

```bash
python analytics/issue-130-sr-support-portfolio/analysis.py
```

Пересобрать replay и записать `full_run.json`:

```bash
python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source 129
```

Полная повторная генерация кандидатов требует PostgreSQL:

```bash
python analytics/issue-130-sr-support-portfolio/generate_inputs.py --source db --workers 4
```

Notebook:

```bash
python analytics/issue-130-sr-support-portfolio/build_notebook.py --execute
```
