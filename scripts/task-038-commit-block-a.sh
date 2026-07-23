#!/usr/bin/env bash
set -u

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

TASK_ID="task-038-commit-block-a"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"
PR_BODY_FILE="${REPORT_DIR}/pr_body.md"
CHANGED_LIST_FILE="${REPORT_DIR}/changed_files.txt"

: > "${LOG_TXT}"

BASE_BRANCH="${BASE_BRANCH:-main}"
FEATURE_BRANCH="${FEATURE_BRANCH:-feat/block-a-frontend-docs-scripts}"
NEXT_BRANCH="${NEXT_BRANCH:-dev/task-039-next}"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-feat(block-a): frontend signals UI, project docs, roadmap and scanner scripts}"
INCLUDE_REPORTS="${INCLUDE_REPORTS:-false}"
CREATE_NEXT_BRANCH="${CREATE_NEXT_BRANCH:-true}"

STATUS="success"
STAGE="done"
ERROR_MESSAGE=""

ORIGINAL_BRANCH=""
FINAL_BRANCH=""
COMMIT_HASH=""
CHANGED_FILES_COUNT=0
PR_URL=""
NEXT_BRANCH_CREATED="false"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

branch_exists() {
  local b="$1"
  git show-ref --verify --quiet "refs/heads/${b}" && return 0
  git ls-remote --exit-code --heads origin "${b}" >/dev/null 2>&1 && return 0
  return 1
}

write_report() {
  local FINISHED_AT
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if command -v python3 >/dev/null 2>&1; then PY=python3;
  elif command -v python >/dev/null 2>&1; then PY=python;
  else PY=""; fi

  export TASK_ID STATUS STAGE ERROR_MESSAGE STARTED_AT FINISHED_AT
  export ORIGINAL_BRANCH FINAL_BRANCH FEATURE_BRANCH NEXT_BRANCH BASE_BRANCH
  export COMMIT_HASH CHANGED_FILES_COUNT PR_URL NEXT_BRANCH_CREATED INCLUDE_REPORTS
  export REPORT_JSON REPORT_MD

  if [[ -n "${PY}" ]]; then
    "${PY}" - <<'PY_REPORT'
import json, os
r = {
  "task_id": os.environ["TASK_ID"],
  "status": os.environ["STATUS"],
  "stage": os.environ["STAGE"],
  "error_message": os.environ["ERROR_MESSAGE"],
  "started_at": os.environ["STARTED_AT"],
  "finished_at": os.environ["FINISHED_AT"],
  "original_branch": os.environ["ORIGINAL_BRANCH"],
  "feature_branch": os.environ["FEATURE_BRANCH"],
  "next_branch": os.environ["NEXT_BRANCH"],
  "final_branch": os.environ["FINAL_BRANCH"],
  "base_branch": os.environ["BASE_BRANCH"],
  "commit_hash": os.environ["COMMIT_HASH"],
  "changed_files_count": int(os.environ.get("CHANGED_FILES_COUNT", "0") or 0),
  "pr_url": os.environ["PR_URL"],
  "next_branch_created": os.environ.get("NEXT_BRANCH_CREATED", "false") == "true",
  "include_reports": os.environ.get("INCLUDE_REPORTS", "false") == "true",
  "next_action": "Send this report.json. If pr_url is empty but status=needs_human, run: gh auth login (or open the new-PR link from log.txt)."
}
with open(os.environ["REPORT_JSON"], "w", encoding="utf-8") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
md = [
  "# " + r["task_id"], "",
  "Status: " + r["status"], "Stage: " + r["stage"], "",
  "Original branch: " + r["original_branch"],
  "Feature branch: " + r["feature_branch"],
  "Next branch: " + r["next_branch"],
  "Final branch: " + r["final_branch"],
  "Base branch: " + r["base_branch"], "",
  "Commit hash: " + r["commit_hash"],
  "Changed files count: " + str(r["changed_files_count"]),
  "Next branch created: " + str(r["next_branch_created"]), "",
  "PR URL: " + (r["pr_url"] or "<empty>"), "",
]
if r["error_message"]:
    md += ["Error message:", "", r["error_message"], ""]
with open(os.environ["REPORT_MD"], "w", encoding="utf-8") as f:
    f.write("\n".join(md))
PY_REPORT
  else
    cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "${STATUS}",
  "stage": "${STAGE}",
  "error_message": "${ERROR_MESSAGE}",
  "feature_branch": "${FEATURE_BRANCH}",
  "final_branch": "${FINAL_BRANCH}",
  "base_branch": "${BASE_BRANCH}",
  "commit_hash": "${COMMIT_HASH}",
  "changed_files_count": ${CHANGED_FILES_COUNT},
  "pr_url": "${PR_URL}",
  "next_branch_created": ${NEXT_BRANCH_CREATED}
}
JSON
    cat > "${REPORT_MD}" <<MD
# ${TASK_ID}
Status: ${STATUS}
PR URL: ${PR_URL}
MD
  fi
}

fail() {
  STATUS="failed"; STAGE="$1"; ERROR_MESSAGE="$2"
  log "ERROR [$1]: $2"
  FINAL_BRANCH="$(git branch --show-current 2>/dev/null || echo "${FEATURE_BRANCH}")"
  write_report
  exit 0
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"

command -v git >/dev/null 2>&1 || fail "git_not_found" "git is not installed or not in PATH"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "not_a_git_repo" "Not a git repository"

ORIGINAL_BRANCH="$(git branch --show-current 2>/dev/null || echo "")"
[[ -n "${ORIGINAL_BRANCH}" ]] || fail "detached_head" "Detached HEAD. Checkout a branch first."
log "Original branch: ${ORIGINAL_BRANCH}"

[[ -n "$(git config user.name 2>/dev/null || echo "")" ]] || { git config user.name "Trading Terminal Bot"; log "Set local user.name"; }
[[ -n "$(git config user.email 2>/dev/null || echo "")" ]] || { git config user.email "trading-terminal@local"; log "Set local user.email"; }

log "Ensuring .gitignore patterns"
touch .gitignore
add_ignore() {
  local p="$1"
  grep -qxF "${p}" .gitignore 2>/dev/null || { echo "${p}" >> .gitignore; log "  +ignore ${p}"; }
}
add_ignore ".env"
add_ignore ".env.local"
add_ignore ".env.*.local"
add_ignore "frontend/node_modules/"
add_ignore "frontend/dist/"
add_ignore "reports/"
add_ignore "logs/"
add_ignore "logs_internal/"
add_ignore "__pycache__/"
add_ignore "*.pyc"
add_ignore "*.log"
add_ignore "backend/certs/*.pem"
add_ignore ".DS_Store"

log "Fetching origin (best effort)"
git fetch origin --prune >>"${LOG_TXT}" 2>&1 || log "WARN: git fetch failed, continuing"

if ! git ls-remote --exit-code --heads origin "${BASE_BRANCH}" >/dev/null 2>&1; then
  DEF="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "")"
  if [[ -n "${DEF}" ]]; then log "Base ${BASE_BRANCH} not on origin, using ${DEF}"; BASE_BRANCH="${DEF}";
  else log "WARN: base ${BASE_BRANCH} not on origin and default undetected"; fi
fi

if branch_exists "${FEATURE_BRANCH}"; then
  OLD="${FEATURE_BRANCH}"; FEATURE_BRANCH="${FEATURE_BRANCH}-$(date -u +%Y%m%d-%H%M%S)"
  log "Branch ${OLD} exists, using ${FEATURE_BRANCH}"
fi

log "Creating feature branch ${FEATURE_BRANCH}"
git checkout -b "${FEATURE_BRANCH}" >>"${LOG_TXT}" 2>&1 || fail "checkout_feature" "Cannot create/checkout ${FEATURE_BRANCH}"

log "Staging files (excluding secrets, node_modules, dist, reports, logs, certs)"
if [[ "${INCLUDE_REPORTS}" == "true" ]]; then
  git add -A -- . \
    ":(exclude).env" ":(exclude).env.local" \
    ":(exclude)backend/certs" \
    ":(exclude)frontend/node_modules" ":(exclude)frontend/dist" \
    ":(exclude)logs" ":(exclude)logs_internal" \
    >>"${LOG_TXT}" 2>&1 || fail "git_add" "git add failed"
else
  git add -A -- . \
    ":(exclude).env" ":(exclude).env.local" \
    ":(exclude)backend/certs" \
    ":(exclude)frontend/node_modules" ":(exclude)frontend/dist" \
    ":(exclude)reports" \
    ":(exclude)logs" ":(exclude)logs_internal" \
    >>"${LOG_TXT}" 2>&1 || fail "git_add" "git add failed"
fi

CHANGED_FILES_COUNT="$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')"
[[ -n "${CHANGED_FILES_COUNT}" ]] || CHANGED_FILES_COUNT=0
log "Staged files: ${CHANGED_FILES_COUNT}"

if git diff --cached --quiet 2>/dev/null; then
  log "No staged changes"
  COMMIT_HASH="$(git rev-parse HEAD 2>/dev/null || echo "")"
  STATUS="needs_human"; STAGE="no_changes"
  ERROR_MESSAGE="No staged changes to commit. Working tree may already be clean."
  FINAL_BRANCH="${FEATURE_BRANCH}"
  write_report; exit 0
fi

log "Creating commit"
git commit -m "${COMMIT_MESSAGE}" >>"${LOG_TXT}" 2>&1 || fail "git_commit" "git commit failed"
COMMIT_HASH="$(git rev-parse HEAD 2>/dev/null || echo "")"
log "Commit: ${COMMIT_HASH}"

log "Pushing ${FEATURE_BRANCH}"
git push -u origin "${FEATURE_BRANCH}" >>"${LOG_TXT}" 2>&1 || fail "git_push" "git push failed for ${FEATURE_BRANCH}"

log "Building changed-files list for PR body"
if git rev-parse --verify "origin/${BASE_BRANCH}" >/dev/null 2>&1; then
  git diff --name-status "origin/${BASE_BRANCH}...HEAD" >"${CHANGED_LIST_FILE}" 2>/dev/null \
    || git diff --name-status HEAD~1..HEAD >"${CHANGED_LIST_FILE}" 2>/dev/null || true
else
  git diff --name-status HEAD~1..HEAD >"${CHANGED_LIST_FILE}" 2>/dev/null || echo "(diff unavailable)" >"${CHANGED_LIST_FILE}"
fi

cat > "${PR_BODY_FILE}" <<'PRBODY'
## Summary

Накопленная работа Block A (стабилизация и готовность API / визуализация):

- **Frontend (React + TS + Vite + Tailwind):** таблица сигналов с фильтрами-воронками в заголовках колонок, серверной сортировкой по клику, пагинацией, фильтром по датам, колонкой `#Patterns`, фильтром по фиксированному набору паттернов, sticky-заголовком таблицы и замороженной шапкой приложения, карточкой сигнала со свечным графиком (закрытие по Esc).
- **Документация:** `docs/agents/project-context.md` (EN, для агентов) и `docs/project-context.ru.md` (RU), план развития `docs/roadmap/development-plan.{en,ru}.md`.
- **Скрипты:** сканеры проекта и схемы БД, диагностические task-скрипты.
- **Orchestrator:** обновлённый промпт агента-оркестратора.

Режим: sandbox, production-торговля отключена. Изменения только аналитика + визуализация + документация.

## Что НЕ входит в коммит

`.env` и секреты, `frontend/node_modules`, `frontend/dist`, `reports/`, `logs/`, сертификаты `backend/certs/*.pem`.

## Проверка

- `npm install` проходил успешно (task-032a).
- Frontend проверен визуально в браузере (фильтры, сортировка, пагинация, sticky-шапка).
- Backend API совместимость с фронтом — частично; полная верификация запланирована следующим шагом (Block A verify).

## Следующие шаги

- Block A: верификация `npm run build` и совместимости `/api/signals` с фронтом.
- Block B/C: качество данных, feature store, backtest engine.
- Диагностика отсутствия 1d-сигналов (мало 1d-индикаторов).

## Changed files (vs base)
PRBODY
cat "${CHANGED_LIST_FILE}" >> "${PR_BODY_FILE}" 2>/dev/null || true

PR_URL="$(gh pr list --head "${FEATURE_BRANCH}" --state open --json url --jq '.[0].url' 2>>"${LOG_TXT}" || echo "")"

if [[ -n "${PR_URL}" ]]; then
  log "PR already exists: ${PR_URL}"
else
  if ! command -v gh >/dev/null 2>&1; then
    STATUS="needs_human"; STAGE="gh_missing"
    ERROR_MESSAGE="GitHub CLI (gh) not installed. Branch pushed; create PR manually (see new-PR link in log.txt) or install gh."
    log "ERROR: ${ERROR_MESSAGE}"
  elif ! gh auth status >>"${LOG_TXT}" 2>&1; then
    STATUS="needs_human"; STAGE="gh_auth"
    ERROR_MESSAGE="gh not authenticated. Run: gh auth login. Branch already pushed."
    log "ERROR: ${ERROR_MESSAGE}"
  else
    log "Creating pull request"
    PR_URL="$(gh pr create --base "${BASE_BRANCH}" --head "${FEATURE_BRANCH}" \
      --title "${COMMIT_MESSAGE}" --body-file "${PR_BODY_FILE}" 2>>"${LOG_TXT}" || echo "")"
    if [[ -n "${PR_URL}" ]]; then log "PR created: ${PR_URL}";
    else STATUS="needs_human"; STAGE="pr_create"; ERROR_MESSAGE="gh pr create failed. See log.txt."; log "ERROR: ${ERROR_MESSAGE}"; fi
  fi
fi

if [[ "${STATUS}" != "failed" && "${CREATE_NEXT_BRANCH}" == "true" ]]; then
  if branch_exists "${NEXT_BRANCH}"; then
    OLD="${NEXT_BRANCH}"; NEXT_BRANCH="${NEXT_BRANCH}-$(date -u +%Y%m%d-%H%M%S)"
    log "Next branch ${OLD} exists, using ${NEXT_BRANCH}"
  fi
  log "Creating next dev branch ${NEXT_BRANCH}"
  if git checkout -b "${NEXT_BRANCH}" >>"${LOG_TXT}" 2>&1; then
    NEXT_BRANCH_CREATED="true"; FINAL_BRANCH="${NEXT_BRANCH}"
    git push -u origin "${NEXT_BRANCH}" >>"${LOG_TXT}" 2>&1 || log "WARN: cannot push next branch ${NEXT_BRANCH}"
    log "Next branch ready: ${NEXT_BRANCH}"
  else
    log "WARN: cannot create next branch ${NEXT_BRANCH}"
    FINAL_BRANCH="$(git branch --show-current 2>/dev/null || echo "${FEATURE_BRANCH}")"
  fi
else
  FINAL_BRANCH="$(git branch --show-current 2>/dev/null || echo "${FEATURE_BRANCH}")"
fi

[[ -n "${FINAL_BRANCH}" ]] || FINAL_BRANCH="${FEATURE_BRANCH}"

write_report
log "Report JSON: ${REPORT_JSON}"
log "Status: ${STATUS}"
log "Done"
exit 0
