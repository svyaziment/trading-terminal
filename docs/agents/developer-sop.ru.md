# Стандартная операционная процедура (SOP) — команда FoxEdge

Обязательна для всех разработчиков (людей и AI-агентов). Любое отклонение без согласования с TeamLead приводит к отклонению PR.

## 🦊 Состав команды

| Роль | Имя | Агент |
| --- | --- | --- |
| Product Owner | Alex | Человек (Alexander Lisitsyn) |
| Team Lead | Reynard | Qwen AI |
| Backend Dev | Arctic | Qwen AI |
| Frontend Dev | Fennec | Qwen AI |
| Data Analyst | Vulpec | Qwen AI |

## 📁 Обновление документации

**Правило:** После каждого успешно завершённого Issue, который меняет архитектуру, API, схему БД, пайплайн данных или операционное поведение, разработчик ОБЯЗАН обновить документацию проекта в `docs/`.

**Матрица обновления документации:**

| Изменение в Issue | Документы для обновления |
| --- | --- |
| Новый модуль/файл | `docs/project-context.md` §2 (File Structure) |
| Добавлен/изменён API-эндпоинт | `docs/project-context.md` §5 (API Endpoints) |
| Изменение схемы БД | `docs/project-context.md` §3 (Database Schema) |
| Изменение пайплайна данных | `docs/project-context.md` §4 + `docs/handover.md` §4 |
| Новый операционный gotcha | `docs/handover.md` §10 (Operational Gotchas) |
| Изменение статуса roadmap | `docs/project-context.md` §8 (Roadmap Status) |
| Изменение стратегии | `docs/strategy/*.md` |

**Правила:**
- Держи ОБЕ языковые версии синхронными: `*.md` (EN) и `*.ru.md` (RU).
- Обновляй заголовок `Last refreshed: <date> (task-NNN)` в каждом изменённом документе.
- Обновление документации входит в ТОТ ЖЕ PR (или в follow-up PR, привязанный к тому же Issue).
- Ссылайся вместо дублирования (handover.md ссылается на секции project-context.md).

**Заметка про отчёты:** Папка `reports/` находится в `.gitignore`. Отчёты агентов по задачам (report.json, log.txt, context.json) — это ЛОКАЛЬНЫЕ артефакты для диагностики, они НЕ коммитятся в репозиторий. Они передаются через комментарии к PR, а не через структуру репозитория.

## 🔄 5-шаговый алгоритм задачи

### Шаг 1. Анализ задачи
1. Открой `https://github.com/svyaziment/trading-terminal/issues/<N>`
2. Изучи описание, критерии приёмки, связанные файлы.
3. Если неясно — перейди в режим обсуждения в комментариях к Issue. НЕ пиши код.

### Шаг 2. Создание и публикация ветки
```bash
git checkout main
git pull origin main
git checkout -b feature/issue-<N>-<short-description>
git push -u origin feature/issue-<N>-<short-description>
```

### Шаг 3. Сбор контекста (ОБЯЗАТЕЛЬНО для multi-element задач)
Если задача затрагивает более одного файла/модуля/таблицы, угадывать запрещено.
```bash
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"
TASK_ID="task-<N>-context"
python docs/refresh/context_collector.py \
  --task-id "${TASK_ID}" \
  --files backend/app/analytics/target_file.py,backend/app/api/target_api.py \
  --tables target_table_1,target_table_2 \
  --output context.json
```

### Шаг 4. Создание скрипта решения
- Начинай с `export MSYS_NO_PATHCONV=1` и `export MSYS2_ARG_CONV_EXCL="*"`.
- Используй heredoc в кавычках (`<<'PYEOF'`) для кода.
- Атомарные патчи: проверяй, что каждый regex-якорь находит РОВНО 1 совпадение до применения. Если 0 или >1 — прервись с `PATCH_FAIL`.
- После создания/изменения файла: `wc -c path/to/file`.
- После изменений backend: `docker compose up -d --build backend`.
- Если скрипт превышает ~100 строк логики, разбей на последовательные шаги.

### Шаг 5. Обновление документации
После завершения Issue обнови документацию проекта в `docs/` согласно матрице обновления документации выше. Обе языковые версии (EN и RU) должны быть обновлены.

## ✅ Чек-лист перед отправкой

- [ ] Ветка создана от свежего `main` с корректным именем.
- [ ] Контекст собран и проанализирован (без угадывания).
- [ ] Скрипт содержит `MSYS_NO_PATHCONV=1` и `MSYS2_ARG_CONV_EXCL="*"`.
- [ ] Все heredoc для кода в кавычках (`<<'EOF'`).
- [ ] Regex-патчи проверены (ровно 1 совпадение) перед записью.
- [ ] Проверен размер файла (`wc -c`) после heredoc.
- [ ] Выполнена пересборка Docker (если менялся Python backend).
- [ ] Документация обновлена (`project-context.md` + `.ru.md`, `handover.md` + `.ru.md`), если Issue меняет архитектуру/API/схему/пайплайн.

## ⛔ Красные линии (мгновенный Reject)

1. Правка кода без сбора контекста для multi-element задач.
2. Хардкод тикеров/параметров вне `trading_config.py` / `trading_universe`.
3. Изменение логики `StrategyEvaluator` без бит-в-бит регрессионного теста (`regression_match: true`).
4. Отсутствие проверки размера файла (`wc -c`) после heredoc.
5. Необновление `project-context.md` / `project-context.ru.md` после Issues, меняющих архитектуру/API/схему/пайплайн.

Любая задача, не следующая этому SOP, будет возвращена на доработку без код-ревью.
