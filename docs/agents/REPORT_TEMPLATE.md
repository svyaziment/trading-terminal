# Шаблон отчёта исполнителя

Каждая задача кладёт артефакты в `reports/issues/<имя-ветки>/`
(напр. `reports/issues/feature/pattern-registry/`).

## report.json (обязателен)
```json
{
  "issue": 12,
  "branch": "feature/pattern-registry",
  "status": "success",
  "started_at": "2026-08-02T10:00:00Z",
  "finished_at": "2026-08-02T10:40:00Z",
  "checks": {
    "py_compile_rc": 0,
    "import_rc": 0,
    "tsc_rc": null,
    "rebuild_rc": 0,
    "regression_match": null
  },
  "acceptance": {
    "GET /api/patterns возвращает схему": true,
    "normalize_patterns на старом и новом формате": true,
    "старые стратегии не ломаются": true
  },
  "changes": [
    "новый модуль backend/app/analytics/pattern_registry.py",
    "GET /api/patterns в strategy_jobs.py",
    "normalize_patterns() + интеграция в save_strategy/_run_job"
  ],
  "files_changed": [
    "backend/app/analytics/pattern_registry.py",
    "backend/app/api/strategy_jobs.py"
  ],
  "artifacts": [
    "reports/issues/feature/pattern-registry/report.json",
    "reports/issues/feature/pattern-registry/log.txt",
    "reports/issues/feature/pattern-registry/api_patterns.json"
  ],
  "pr": "https://github.com/svyaziment/trading-terminal/pull/NN",
  "note": "контракт patterns:{id:params} зафиксирован; confirm_windows теперь в patterns.levels_reversal",
  "next_action": "разблокирует #14 (движок) и #15 (модалка)"
}
```

Поля:
- `status`: `success` | `needs_review` | `blocked` | `failed`.
- `checks.*_rc`: код возврата проверки (`0` = ок, `null` = неприменимо).
- `acceptance`: каждый критерий приёмки из issue → `true/false`.
- `regression_match`: обязательно для `critical` (#14).

## log.txt (обязателен)
Построчный журнал: что делалось и с каким результатом.
```
Task: #12 pattern-registry (branch feature/pattern-registry)
Collecting context... done
Creating pattern_registry.py ... OK
Adding GET /api/patterns ... OK
py_compile backend ... rc=0
rebuild container ... rc=0
import check in container ... OK
curl GET /api/patterns ... 200, schema returned
normalize_patterns unit check (old list / new dict) ... OK
report.json written
Done
```

## Дополнительные артефакты (по задаче)
- #13: SQL-дамп `SELECT timeframe,count(*),min(timestamp),max(timestamp) ...`.
- #14: `regression_verdict.json` (`{"match": true, ...}`) + `baseline.json`/`after.json`.
- #12: `api_patterns.json` (ответ `GET /api/patterns`).
- #15: `tsc.log` (пустой при успехе) + скрин/описание поведения модалки.
