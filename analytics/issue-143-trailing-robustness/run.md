# #143 · Протокол прогона robustness-решётки трейлинга
Сгенерирован: 2026-09-07T08:12:16+00:00 (референс — #139)

## Окружение

- stage: `all` · exit code: `0`
- python `3.11.7` · platform `win32` · BIOSIM_ENV `-`
- PostgreSQL (хост/база, без пароля): `(не задан)` · доступ только read-only (SELECT)
- каталог #139: `analytics/issue-139-trailing-stop-new-level` · выходной каталог: `analytics/issue-143-trailing-robustness`

## Отпечатки входных артефактов

| артефакт | sha256[:16] |
| --- | --- |
| `analysis.py` | `13b2374a5bafb9c9` |
| `extract_inputs.py` | `11df4894fdfe3e78` |
| `inputs.json` | `2ab00e941ca590e0` |
| `results.json` | `acd6bc5fd8756811` |
| `summary.json` | `4e3321f36f9d4f77` |
| `trailing.py` | `b77e659a83189675` |
| `grids.json` | `f33bfdaccfd3198a` |

## Скоуп

- `tickers` = ["AFKS", "ALRS", "CBOM", "CHMF", "FEES", "FLOT", "GAZP", "GMKN", "IRAO", "LKOH", "MGNT", "MOEX", "MTLR", "MTSS", "NLMK", "NVTK", "PHOR", "PIKK", "PLZL", "ROSN", "RTKM", "RUAL", "SBER", "SIBN", "SNGS", "TATN", "TRNFP", "VTBR"]
- `period` = ["2024-08-01", "2026-08-21"]
- `n_tickers_cached` = 28
- `n_candidates_ref` = 3305
- `full_scope` = True
- `path_truncated` = 0
- `limit` = 0
- кандидатов из книги #139: `3305`
- тикеров с кэшем путей: `28`
- elapsed: `160.62` сек

## Итог

- статус прогона: `success`
- паритет с #139: `OK` (проверено сделок: 3305; по механике выхода: 0; сверх допуска по доходности 0.1 п.п.: 0; макс. |Δ доходности| = 0.0969 п.п.)
- контракт конфигурации: `OK` (проверок 26, пройдено 26)
- сеток в решётке: 5 · стресс-прогонов: 60 · окон walk-forward: 9

### Извлечение путей

```json
{
 "tickers": 28,
 "from_cache": 28,
 "extracted": 0,
 "paths_cached": 28,
 "paths_extracted": 0,
 "paths_missing": 0,
 "failed": [],
 "paths_failed": 0,
 "candidates": 3305,
 "engine_replay_mismatches": 0,
 "seconds": 2.1
}
```

## Артефакты

- `contract_json` → `analytics/issue-143-trailing-robustness/contract.json`
- `exits_jsonl` → `analytics/issue-143-trailing-robustness/exits.jsonl.gz`
- `extract_summary` → `analytics/issue-143-trailing-robustness/extract_summary.json`
- `grids_csv` → `analytics/issue-143-trailing-robustness/grids.csv`
- `report_json` → `analytics/issue-143-trailing-robustness/report.json.gz`
- `report_md` → `analytics/issue-143-trailing-robustness/report.md`
- `run_md` → `analytics/issue-143-trailing-robustness/run.md`
- `summary_json` → `analytics/issue-143-trailing-robustness/summary.json`
- `walkforward_csv` → `analytics/issue-143-trailing-robustness/walkforward.csv`
