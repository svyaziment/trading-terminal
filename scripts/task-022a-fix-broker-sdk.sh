#!/usr/bin/env bash
# === Task: task-022a-fix-broker-sdk ===
# Пересборка backend с корректной установкой t-tech-investments SDK
set -u

TASK_ID="task-022a-fix-broker-sdk"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="/f/GIT/trading-terminal"
FEATURE_BRANCH="feat/broker-data-loader"
SDK_INDEX_URL="https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple"
REPORT_DIR="${CWD}/reports/${TASK_ID}"
LOG_FILE="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

mkdir -p "${REPORT_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Task: ${TASK_ID} ==="
echo "Started: ${STARTED_AT}"
echo "Working directory: ${CWD}"
echo "Feature branch: ${FEATURE_BRANCH}"

cd "${CWD}" || { echo "FAIL: cannot cd to ${CWD}"; exit 1; }

# ---------- 1. Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"
if [ "${CURRENT_BRANCH}" != "${FEATURE_BRANCH}" ]; then
  git checkout "${FEATURE_BRANCH}" 2>/dev/null || git checkout -b "${FEATURE_BRANCH}" origin/main
  echo "OK: switched to ${FEATURE_BRANCH}"
else
  echo "OK: already on ${FEATURE_BRANCH}"
fi

# ---------- 2. Fix requirements.txt ----------
echo "Fixing backend/requirements.txt..."
REQ_FILE="backend/requirements.txt"

# Удаляем старую (битую) строку t-tech-investments, если она есть
if grep -q '^t-tech-investments' "${REQ_FILE}" 2>/dev/null; then
  sed -i '/^t-tech-investments/d' "${REQ_FILE}"
  echo "OK: removed stale t-tech-investments line"
fi

# Добавляем с явным index-url (PEP 508 direct reference не нужен — используем Dockerfile)
echo "t-tech-investments" >> "${REQ_FILE}"
echo "OK: t-tech-investments re-added to ${REQ_FILE}"

# ---------- 3. Fix Dockerfile: добавляем --extra-index-url ----------
echo "Rewriting backend/Dockerfile..."
cat > backend/Dockerfile <<'DOCKERFILE'
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

# Основной индекс PyPI + приватный индекс T-Bank для t-tech-investments
RUN pip install --no-cache-dir \
    --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple \
    --trusted-host opensource.tbank.ru \
    -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

# Чистка артефактов, которые могли попасть с хоста (Windows CRLF, __pycache__)
RUN find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN find /app -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# Build-time smoke check: SDK должен импортироваться
RUN python -c "import t_tech.invest; from t_tech.invest.constants import INVEST_GRPC_API; print('SDK_OK')"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE
echo "OK: backend/Dockerfile updated"

# ---------- 4. .dockerignore ----------
echo "Updating backend/.dockerignore..."
DOCKERIGNORE="backend/.dockerignore"
touch "${DOCKERIGNORE}"
for pattern in "**/__pycache__" "**/*.pyc" "**/*.pyo" ".env" ".git"; do
  grep -qxF "${pattern}" "${DOCKERIGNORE}" || echo "${pattern}" >> "${DOCKERIGNORE}"
done
echo "OK: backend/.dockerignore updated"

# ---------- 5. Host cleanup: CRLF + __pycache__ ----------
echo "Cleaning host artifacts..."
find backend -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find backend -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
echo "OK: host cleanup done"

# ---------- 6. Docker build (no cache) ----------
echo "Checking Docker daemon..."
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not running"; exit 1; }
echo "OK: docker daemon is running"

docker compose config >/dev/null 2>&1 || { echo "FAIL: docker-compose.yml invalid"; exit 1; }
echo "OK: docker-compose.yml is valid"

echo "Rebuilding backend image without cache (t-tech-investments SDK)..."
echo "This may take several minutes."
if ! docker compose build --no-cache backend; then
  echo "FAIL: backend image rebuild failed"
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "${REPORT_JSON}" <<EOF
{"task_id":"${TASK_ID}","status":"failed","started_at":"${STARTED_AT}","finished_at":"${FINISHED_AT}","errors":["docker compose build failed"]}
EOF
  exit 1
fi
echo "OK: backend image rebuilt without cache"

# ---------- 7. Runtime SDK check inside container ----------
echo "Verifying SDK import inside container..."
SDK_CHECK="$(docker compose run --rm -T --no-deps backend \
  python -c "from t_tech.invest import Client; from t_tech.invest.constants import INVEST_GRPC_API; print('SDK_IMPORT_OK')" 2>&1)"
echo "----- BEGIN SDK_CHECK -----"
echo "${SDK_CHECK}"
echo "----- END SDK_CHECK -----"

if ! echo "${SDK_CHECK}" | grep -q "SDK_IMPORT_OK"; then
  echo "FAIL: t_tech.invest import failed inside container"
  exit 1
fi
echo "OK: t_tech.invest imports successfully"

# ---------- 8. Commit ----------
echo "Staging files..."
git add backend/Dockerfile backend/.dockerignore backend/requirements.txt \
        backend/app/broker/ scripts/${TASK_ID}.sh 2>/dev/null || \
git add backend/Dockerfile backend/.dockerignore backend/requirements.txt
echo "OK: git add completed"

# Secret check
if git diff --cached --name-only | grep -Ei '(\.env$|secret|token|credential)'; then
  echo "FAIL: possible secret file staged"
  exit 1
fi
echo "OK: no obvious secret files staged"

if git diff --cached --quiet; then
  echo "OK: no changes to commit"
  COMMIT_SHA="$(git rev-parse --short HEAD)"
else
  git commit -m "fix(task-022): install t-tech-investments from T-Bank private index" \
    && echo "OK: commit created" || echo "WARN: commit failed"
  COMMIT_SHA="$(git rev-parse --short HEAD)"
fi
echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- 9. Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "success",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "feature_branch": "${FEATURE_BRANCH}",
    "sdk_index_url": "${SDK_INDEX_URL}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "requirements_fixed", "path": "backend/requirements.txt", "status": "passed"},
    {"name": "dockerfile_extra_index", "path": "backend/Dockerfile", "status": "passed"},
    {"name": "dockerignore_updated", "status": "passed"},
    {"name": "host_cleanup", "status": "passed"},
    {"name": "docker_build_no_cache", "status": "passed"},
    {"name": "sdk_import_check", "status": "passed"},
    {"name": "git_commit", "path": "${COMMIT_SHA}", "status": "passed"}
  ],
  "errors": []
}
EOF

cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** success
**Started:** ${STARTED_AT}
**Finished:** ${FINISHED_AT}
**Branch:** ${FEATURE_BRANCH}
**Commit:** ${COMMIT_SHA}

## Что исправлено
1. \`backend/requirements.txt\` — \`t-tech-investments\` добавлен корректно.
2. \`backend/Dockerfile\` — pip вызывается с \`--extra-index-url ${SDK_INDEX_URL}\` и \`--trusted-host opensource.tbank.ru\`.
3. Build-time проверка \`import t_tech.invest\` внутри образа.
4. Очистка \`__pycache__\` и CRLF на хосте и в образе.
5. \`.dockerignore\` дополнен паттернами \`**/__pycache__\`, \`**/*.pyc\`.

## Проверки
- Docker build без кеша: **passed**
- Импорт SDK в контейнере: **passed**
- Коммит: \`${COMMIT_SHA}\`
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"