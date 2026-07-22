#!/usr/bin/env bash

set -u

TASK_ID="task-023-broker-data-loader-check"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
FEATURE_BRANCH="feat/broker-data-loader"
REPORT_DIR="${CWD}/reports/${TASK_ID}"
LOG_FILE="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

mkdir -p "${REPORT_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Task: ${TASK_ID} ==="
echo "Started: ${STARTED_AT}"
echo "Working directory: ${CWD}"

cd "${CWD}" || { echo "FAIL: cannot cd to ${CWD}"; exit 1; }

# ---------- Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"

[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

if [ "${CURRENT_BRANCH}" != "${FEATURE_BRANCH}" ]; then
    if git show-ref --verify --quiet "refs/heads/${FEATURE_BRANCH}"; then
        git checkout "${FEATURE_BRANCH}"
        echo "OK: switched to ${FEATURE_BRANCH}"
    else
        git checkout -b "${FEATURE_BRANCH}" origin/main
        echo "OK: created ${FEATURE_BRANCH} from origin/main"
    fi
else
    echo "OK: already on ${FEATURE_BRANCH}"
fi

# ---------- Check broker module ----------
echo "Checking broker module..."

if [ ! -f backend/app/broker/__init__.py ]; then
    mkdir -p backend/app/broker
    touch backend/app/broker/__init__.py
    echo "OK: created backend/app/broker/__init__.py"
fi

if [ ! -f backend/app/broker/data_loader.py ]; then
    echo "FAIL: backend/app/broker/data_loader.py not found"
    echo "Run task-022a-fix-broker-sdk again or restore data_loader.py"
    exit 1
fi

echo "OK: backend/app/broker/data_loader.py exists"

# ---------- Commit broker module if needed ----------
echo "Staging broker module..."
git add backend/app/broker/ scripts/${TASK_ID}.sh 2>/dev/null || git add backend/app/broker/

if git diff --cached --name-only | grep -Ei '(\.env$|secret|token|credential)'; then
    echo "FAIL: possible secret file staged"
    exit 1
fi

echo "OK: no obvious secret files staged"

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "feat(task-023): add broker data_loader module" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- Docker checks ----------
echo "Checking Docker..."
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not running"; exit 1; }
echo "OK: docker daemon is running"

docker compose config >/dev/null 2>&1 || { echo "FAIL: docker-compose.yml invalid"; exit 1; }
echo "OK: docker-compose.yml is valid"

echo "Building backend image..."
if ! docker compose build backend; then
    echo "FAIL: backend image build failed"
    exit 1
fi

echo "OK: backend image built"

# ---------- Check TINVEST_TOKEN ----------
echo "Checking TINVEST_TOKEN..."

TOKEN_CHECK="$(docker compose run --rm -T --no-deps backend \
    python -c "import os; print('TOKEN_SET=' + ('true' if os.getenv('TINVEST_TOKEN') else 'false'))" 2>&1)"

echo "${TOKEN_CHECK}"

if ! echo "${TOKEN_CHECK}" | grep -q "TOKEN_SET=true"; then
    echo "FAIL: TINVEST_TOKEN is not set inside backend container"
    echo "Add TINVEST_TOKEN to .env and restart"
    exit 1
fi

echo "OK: TINVEST_TOKEN is set"

# ---------- Test DataLoader ----------
echo "Testing DataLoader: fetching 30min candles for VTBR (5 days)..."

LOADER_CHECK="$(docker compose run --rm -T --no-deps backend python - <<'PY'
from app.broker.data_loader import DataLoader

try:
    dl = DataLoader()
    print("DATALOADER_INIT=ok")

    df = dl.fetch_candles_by_figi(
        figi="BBG004730ZJ9",
        ticker="VTBR",
        days=5,
        interval_str="30min"
    )

    print("CANDLES_LOADED=" + str(len(df)))
    print("DATALOADER_STATUS=success")
except Exception as e:
    print("DATALOADER_INIT=error")
    print("DATALOADER_STATUS=error")
    print("DATALOADER_ERROR=" + str(e))
PY
)"

echo "----- BEGIN LOADER_CHECK -----"
echo "${LOADER_CHECK}"
echo "----- END LOADER_CHECK -----"

DATALOADER_STATUS="$(echo "${LOADER_CHECK}" | grep 'DATALOADER_STATUS=' | cut -d= -f2)"
CANDLES_LOADED="$(echo "${LOADER_CHECK}" | grep 'CANDLES_LOADED=' | cut -d= -f2)"
DATALOADER_ERROR="$(echo "${LOADER_CHECK}" | grep 'DATALOADER_ERROR=' | cut -d= -f2-)"

if [ "${DATALOADER_STATUS}" != "success" ]; then
    echo "FAIL: DataLoader failed: ${DATALOADER_ERROR}"
    FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "branch": "${FEATURE_BRANCH}",
    "dataloader_status": "${DATALOADER_STATUS}",
    "candles_loaded": "${CANDLES_LOADED:-0}",
    "dataloader_error": "${DATALOADER_ERROR}"
  },
  "errors": ["${DATALOADER_ERROR}"]
}
EOF

    exit 1
fi

echo "OK: DataLoader loaded ${CANDLES_LOADED} candles"

# ---------- Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "success",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "branch": "${FEATURE_BRANCH}",
    "token_set": true,
    "dataloader_status": "${DATALOADER_STATUS}",
    "candles_loaded": "${CANDLES_LOADED}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "docker_daemon", "status": "passed"},
    {"name": "token_set", "status": "passed"},
    {"name": "dataloader_init", "status": "passed"},
    {"name": "candles_loaded", "status": "passed", "count": "${CANDLES_LOADED}"}
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

## Checks

- Git repository: **passed**
- Docker daemon: **passed**
- TINVEST_TOKEN set: **passed**
- DataLoader init: **passed**
- Candles loaded: **${CANDLES_LOADED}**

## Errors

None
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
