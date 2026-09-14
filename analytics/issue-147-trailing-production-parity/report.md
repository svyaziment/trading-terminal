# Issue #147 — Паритет боевого пути трейлинг-стопа с книгой #139 (гейт эпика #142)

Вердикт гейта: **OVERALL=FAIL** (A_prod=PASS, B_prod=FAIL, B_default=PASS).
Вердикт B_prod вынесен на подпись TL/PO: черновик комментария —
`reports/147-issue-147-trailing-parity/tl_po_comment_draft.md`.

## 1. Скоуп и метод

Пакет `analytics/issue-147-trailing-production-parity/` прогоняет боевой путь
(`StrategyEvaluator` → `portfolio_simulator`, shard-процессы v4 без multiprocessing) на запертой
`test_20260830_new_level` (id=126, SHA `dfc855195ade…`), период `2024-08-01` … `timestamp < 2026-08-21`,
28 тикеров, 50 000 ₽ / слот 10 000 ₽ / макс 5. Три книги: A_prod (трейлинг выключен),
B_prod (сетка `ref139` инжектирована явно), B_default (ступени импортированы из
`trading_config.TRAILING_STOP` = `ultra_late_tight`). Кэш версионирован
`cache/<book>-<sha8 конфига>/`; защищённые строки 126/36/102/118 сверены до и после
(`protected_untouched=True`), записей в `paper_positions` / `backtest_results` нет.

## 2. Критерии гейта (задекларированы в run.md v3 ДО прогона)

- A (tight): equity ±206.35 ₽, n 0, PF ±0.05, WR ±0.5 п.п., daily MaxDD ±0.5 п.п., причины выхода точные, входы на уровне кандидатов 0/0.
- B (направленный): equity > A, PF 1.45–1.65, dailyDD ≤ 4.0 и |dd − 2.74| ≤ 1.0 п.п., n ±20%, initial_stop ≥ 50%.
- D (направленный): equity > A и в ±10% от 110 434 ₽, PF ±0.15 от 1.60, n ±20%, |dd − 3.06| ≤ 1.0 п.п., порядок сплита stop > trailing > take.

## 3. Результаты

### A_prod vs #139 A — PASS
| final_equity_rub | 95179.91 | 95180.01 | 0.1 | 206.35 | yes |
| n_trades | 2649 | 2649 | 0.0 | 0 | yes |
| profit_factor | 1.41 | 1.41 | 0.0 | 0.05 | yes |
| win_rate | 24.2 | 24.2 | 0.0 | 0.5 | yes |
| max_drawdown_pct | 6.49 | 6.49 | 0.0 | 0.5 | yes |
- Причины выхода: prod {'stop': 2009, 'take': 640} = ref, match=True.
- Входы (кандидаты): new/missing = 0/0.

### B_prod vs #139 B (ref139) — FAIL
| final_equity_rub | 111458.96 | 103176.0 | 8282.96 | 206.35 | NO |
| n_trades | 3678 | 3118 | 560.0 | 0 | NO |
| profit_factor | 1.5 | 1.54 | 0.04 | 0.05 | yes |
| win_rate | 42.0 | 42.3 | 0.3 | 0.5 | yes |
| max_drawdown_pct | 4.01 | 2.74 | 1.27 | 0.5 | NO |
- Причины (mapped): initial_stop 2097 / take 238 / trailing 1343 против ref 1762 / 205 / 1151.
- Входы (кандидаты): new/missing = 789/15; book-level 706/333.

### B_default vs #143 ultra_late_tight — PASS
| final_equity_rub | 120753.7 | 110433.68 | 10320.02 | 206.35 | NO |
| n_trades | 3794 | 3162 | 632.0 | 0 | NO |
| profit_factor | 1.56 | 1.6 | 0.04 | 0.05 | yes |
| win_rate | 42.3 | 42.6 | 0.3 | 0.5 | yes |
| max_drawdown_pct | 3.96 | 3.06 | 0.9 | 0.5 | NO |
- Причины (mapped): initial_stop 2165 / take 86 / trailing 1543 против ref 1790 / 76 / 1296.
- Замечание: n 3794 против 3162 — Δ632 при лимите 632.4 (впритык, зафиксировано).

## 4. Разбор расхождений (п.5 задачи)

- **Конвенции выхода**: расхождений нет — `grid_check` #145: 0/3305 на обеих сетках; блокирующий комментарий в #145 не нужен.
- **Местные детали прода** (лот/округление/комиссии): вклад в equity ≤ 0.1 ₽ (книга A), внутри допуска.
- **Структурный остаток B_prod**: обратная связь live-контура — ранний trailing-выход освобождает тикер, движок берёт новые входы (+789/−15 кандидатов против 3305 оверлея); n 3678 против 3118, daily MaxDD 4.01 против 2.74 п.п. Оверлей #139/#143 держит входы фиксированными по построению (single-difference), live-контур не может; поэтому плотные допуски ±206.35 ₽ и полоса DD для B структурно недостижимы. Критерии были пересказаны направленно ДО прогона; полоса |dd−2.74|≤1.0 всё равно не пройдена (Δ1.27) → честный FAIL, допуски постфактум не подгоняются.
- **B_default**: PASS по всем направленным критериям; боевая лестница `ultra_late_tight` в live-контуре воспроизводит порядок и масштаб оверлея.

## 5. Воспроизведение

- Прогон: shard-раннер v4 (логи `reports/147-issue-147-trailing-parity/run_v4.log`, `shards.log`), сборка `python analytics/issue-147-trailing-production-parity/run.py --stage assemble`.
- Отчёт без БД: `--stage report`. unit: `cd backend && python -m pytest -q tests/test_trailing_parity.py tests/test_issue139_analysis.py tests/test_issue155_analysis.py`.
- `extract.json` (5.7 MB) хранится локально; в репозиторий идёт `extract.json.gz` (практика #143).

## 6. Документация и SOP

- project-context RU/EN: подраздел «Паритет боевого пути» в §18 + строка блока W (§8).
- handover RU/EN: подраздел паритета в §35 + gotcha §10 (Windows spawn WinError 5 → shard-процессы).
- SOP RU/EN: раздел ⏳ detached-прогонов + пункт чек-листа + красная линия; RU-фикс двойного заголовка.

## 7. Долги и ограничения

- Вердикт B_prod ждёт решения TL/PO (черновик комментария приложен): принять live-loop книгу как референс боевого пути (пересказ критериев проспективно, наблюдение DD уходит в #152) или блокировать волну 1 (#148/#151).
- Наблюдение daily DD live-контурa (4.01 п.п. у B_prod, 3.96 п.п. у B_default против 2.74/3.06 оверлея) наследуется приёмкой #152.
