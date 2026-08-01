#!/usr/bin/env bash
set -uo pipefail
OUT="docs/agents"
mkdir -p "${OUT}"

# ============================================================ README (индекс)
cat > "${OUT}/README.md" <<'MD'
# Инструкции команды · Trading Terminal

Эпик в работе: **#11 — Конфигурируемые паттерны** (задачи #12–#15).

| Документ | Зачем |
|---|---|
| [WORKFLOW.md](WORKFLOW.md) | Регламент: как взять задачу, ветвиться, отчитываться, проходить review |
| [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) | Обязательная структура `report.json` + `log.txt` (+ доп. артефакты) |
| [REVIEW_CHECKLIST.md](REVIEW_CHECKLIST.md) | Чек-лист Team Lead при approve |

## Задачи эпика #11
| Issue | Задача | Ветка | Зависит от |
|---|---|---|---|
| #12 | Pattern registry + формат конфига | `feature/pattern-registry` | — |
| #13 | Агрегация 1W/1M свечей | `feature/candles-1w-1m` | — |
| #14 | Параметризованный движок + контекст (+регрессия) | `feature/parameterized-engine` | #12 |
| #15 | Универсальная модалка настроек | `feature/pattern-settings-modal` | #12, #14 |

**Порядок:** `#12` ∥ `#13` (параллельно) → `#14` → `#15`.
MD

# ============================================================ WORKFLOW
cat > "${OUT}/WORKFLOW.md" <<'MD'
# Регламент работы (Workflow)

## Роли
- **Исполнитель** — берёт issue, реализует в ветке, отчитывается, открывает PR.
- **Team Lead** — ревьюет PR + артефакты, делает **Approve** (merge) или **Request changes**.

## Жизненный цикл задачи
1. **Взять задачу.** Назначить себя assignee в issue. Не брать задачу, заблокированную
   зависимостью (см. таблицу в README). Одна задача = один исполнитель.
2. **Ветка.** Ветки уже созданы от `origin/main`. Работать строго в своей:
   ```bash
   git fetch origin
   git checkout feature/<имя-ветки>     # например feature/pattern-registry
   git pull --ff-only                    # если ветка уже пушалась
   ```
   Не коммитить в `main`. Не трогать чужие feature-ветки.
3. **Реализация.** Вести работу по телу issue: *Контекст → Цель → Что сделать →
   Критерии приёмки → Подводные камни → Definition of Done*. Отмечать выполненные
   чекбоксы прямо в теле issue.
4. **Проверки (обязательно, до PR):**
   - Backend: `python -m py_compile <файлы>` + импорт в контейнере
     (`docker compose exec -T backend python -c "import ..."`), rebuild контейнера.
   - Frontend: `tsc --noEmit` (0 ошибок).
   - Для `critical` (#14): регрессия baseline vs after → `regression_match: true`.
5. **Артефакты.** Сформировать в `reports/issues/<имя-ветки>/`:
   - `report.json` — итог (структура в REPORT_TEMPLATE.md);
   - `log.txt` — журнал шагов;
   - доп. файлы по задаче (`regression_verdict.json`, `api_patterns.json`, SQL-дампы и т.п.).
6. **Коммит + PR.**
   ```bash
   git add -A
   git commit -m "#<N> <кратко>: <что сделано>"   # напр. "#12: pattern registry + GET /api/patterns"
   git push -u origin feature/<имя-ветки>
   gh pr create --base main --head feature/<имя-ветки> \
     --title "#<N> <задача>" --body "Closes #<N>"
   ```
   PR обязан ссылаться на issue (`Closes #N` для самостоятельных, `Part of #11` — доп.).
7. **Отметка в issue.** Коммент в issue: краткий итог + ссылка на PR + статус «готово к ревью».
8. **Review.** Team Lead ревьюет (REVIEW_CHECKLIST.md). Итог:
   - **Approve → squash-merge** в main, issue закрывается;
   - **Request changes** — исполнитель правит в той же ветке, пушит, повторно запрашивает ревью.

## Правила
- Маленькие атомарные коммиты; каждый коммит ссылается на `#N`.
- Не менять поведение на дефолтных параметрах без явного указания в issue.
- Обратная совместимость обязательна (старые конфиги `patterns: [...]` работают).
- Никаких секретов/токенов в коде и артефактах.
- `close_pool()` не вызывать в живых циклах; JSON — через `_json_safe`.

## Быстрый старт (окружение)
```bash
git clone <repo> && cd trading-terminal
gh auth login                      # для работы с issues/PR
docker compose up -d --build       # backend + db
# frontend: cd frontend && npm i && npm run dev
```
MD

# ============================================================ REPORT TEMPLATE
cat > "${OUT}/REPORT_TEMPLATE.md" <<'MD'
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
MD

# ============================================================ REVIEW CHECKLIST
cat > "${OUT}/REVIEW_CHECKLIST.md" <<'MD'
# Чек-лист Team Lead (approve)

Перед **Approve + merge** проверить:

## Связка
- [ ] PR ссылается на issue (`Closes #N` / `Part of #11`).
- [ ] PR из правильной ветки `feature/*` в `main`.
- [ ] В issue отмечены выполненные чекбоксы критериев приёмки.

## Отчёт (reports/issues/<branch>/)
- [ ] `report.json`: `status=success`, все применимые `*_rc=0`.
- [ ] `acceptance`: все критерии приёмки = `true`.
- [ ] `log.txt`: видна полная цепочка (context → реализация → проверки → rebuild → validate).
- [ ] Для `critical` (#14): `regression_match=true` (+ `regression_verdict.json`).
- [ ] Доп. артефакты на месте (по REPORT_TEMPLATE).

## Код
- [ ] Нет хардкода там, где issue требует параметризацию/схему.
- [ ] Обратная совместимость: старые конфиги `patterns: [...]` работают (`normalize_patterns`).
- [ ] Нет `close_pool()` в живых циклах; JSON через `_json_safe`/`_to_dict`.
- [ ] Frontend: универсальность (модалка рендерится из схемы, без хардкода полей).
- [ ] Нет мусора, закомментированных блоков, секретов/токенов.
- [ ] Изменения соответствуют *только* своему issue (нет посторонних правок).

## Проверки
- [ ] Backend: `py_compile` + импорт в контейнере + rebuild — зелёные.
- [ ] Frontend: `tsc --noEmit` — 0 ошибок.
- [ ] Регрессия (где требуется) — зелёная.

## Вердикт
- Всё ок → **Approve** → **Squash-merge** → issue закрывается → отметить в эпике #11.
- Есть замечания → **Request changes** с конкретными комментариями → исполнитель правит
  в той же ветке и повторно запрашивает ревью.
MD

echo "Team docs created:"
ls -1 "${OUT}"/*.md
