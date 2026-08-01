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
