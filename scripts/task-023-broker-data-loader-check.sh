#!/usr/bin/env bash
set -u

TASK_ID="task-023-broker-data-loader-check"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
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

# ---------- 1. Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"
[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

# ---------- 2. Docker checks ----------
echo "Checking Docker..."
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not running"; exit 1; }
echo "OK: docker daemon is running"

docker compose config >/dev/null 2>&1 || { echo "FAIL: docker-compose.yml invalid"; exit 1; }
echo "OK: docker-compose.yml is valid"

# ---------- 3. Проверяем TINVEST_TOKEN ----------
echo "Checking TINVEST_TOKEN..."

TOKEN_CHECK="$(docker compose run --rm -T --no-deps backend \
  python -c "
import os
token = os.getenv('TINVEST_TOKEN', '')
if token:
    print('TOKEN_SET=true')
    print('TOKEN_PREFIX=' + token[:8] + '...')
else:
    print('TOKEN_SET=false')
" 2>&1)"

echo "${TOKEN_CHECK}"

TOKEN_SET="$(echo "${TOKEN_CHECK}" | grep 'TOKEN_SET=' | cut -d= -f2)"

if [ "${TOKEN_SET}" != "true" ]; then
  echo "FAIL: TINVEST_TOKEN not set in .env"
  echo "Add TINVEST_TOKEN=your_token to .env and rerun"
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "${REPORT_JSON}" <<EOF
{"task_id":"${TASK_ID}","status":"needs_human","started_at":"${STARTED_AT}","finished_at":"${FINISHED_AT}","errors":["TINVEST_TOKEN not set in .env"]}
EOF
  exit 1
fi

echo "OK: TINVEST_TOKEN is set"

# ---------- 4. Проверяем DataLoader: загрузка свечей ----------
echo "Testing DataLoader: fetching 30min candles for VTBR (5 days)..."

LOADER_CHECK="$(docker compose run --rm -T --no-deps backend \
  python -c "
import sys
import traceback

try:
    from app.broker.data_loader import DataLoader

    loader = DataLoader()
    print('DATALOADER_INIT=ok')

    # Загружаем 30-минутные свечи для VTBR за 5 дней
    df = loader.fetch_candles_by_figi(
        figi='BBG004730ZJ9',
        ticker='VTBR',
        days=5,
        interval_str='30min'
    )

    if df is not None and not df.empty:
        print('CANDLES_LOADED=' + str(len(df)))
        print('COLUMNS=' + ','.join(df.columns.tolist()))
        print('FIRST_ROW=' + str(df.iloc[0].to_dict()))
        print('LAST_ROW=' + str(df.iloc[-1].to_dict()))
        print('DATALOADER_STATUS=success')
    else:
        print('CANDLES_LOADED=0')
        print('DATALOADER_STATUS=empty')
        print('DATALOADER_ERROR=No candles returned')

except Exception as e:
    print('DATALOADER_INIT=error')
    print('DATALOADER_STATUS=error')
    print('DATALOADER_ERROR=' + str(e))
    traceback.print_exc()
" 2>&1)"

echo "----- BEGIN LOADER_CHECK -----"
echo "${LOADER_CHECK}"
echo "----- END LOADER_CHECK -----"

DATALOADER_STATUS="$(echo "${LOADER_CHECK}" | grep 'DATALOADER_STATUS=' | cut -d= -f2)"
CANDLES_LOADED="$(echo "${LOADER_CHECK}" | grep 'CANDLES_LOADED=' | cut -d= -f2)"
DATALOADER_ERROR="$(echo "${LOADER_CHECK}" | grep 'DATALOADER_ERROR=' | cut -d= -f2-)"

# ---------- 5. Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ "${DATALOADER_STATUS}" = "success" ]; then
  STATUS="success"
  echo ""
  echo "✅ DataLoader works! Loaded ${CANDLES_LOADED} candles for VTBR (30min, 5 days)"
elif [ "${DATALOADER_STATUS}" = "empty" ]; then
  STATUS="needs_human"
  echo ""
  echo "⚠️ DataLoader initialized but returned 0 candles."
  echo "Possible reasons: market closed, wrong FIGI, API limits."
else
  STATUS="failed"
  echo ""
  echo "❌ DataLoader failed: ${DATALOADER_ERROR}"
fi

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "${STATUS}",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "branch": "${CURRENT_BRANCH}",
    "token_set": ${TOKEN_SET:-false},
    "dataloader_status": "${DATALOADER_STATUS:-unknown}",
    "candles_loaded": "${CANDLES_LOADED:-0}",
    "dataloader_error": "${DATALOADER_ERROR:-}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "docker_daemon", "status": "passed"},
    {"name": "token_set", "status": "$([ "${TOKEN_SET}" = "true" ] && echo passed || echo failed)"},
    {"name": "dataloader_init", "status": "$([ "${DATALOADER_STATUS}" != "error" ] && echo passed || echo failed)"},
    {"name": "candles_loaded", "status": "$([ "${DATALOADER_STATUS}" = "success" ] && echo passed || echo failed)", "count": "${CANDLES_LOADED:-0}"}
  ],
  "errors": $([ "${STATUS}" = "success" ] && echo "[]" || echo "[\"${DATALOADER_ERROR:-unknown error}\"]")
}
EOF

cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** ${STATUS}
**Started:** ${STARTED_AT}
**Finished:** ${FINISHED_AT}
**Branch:** ${CURRENT_BRANCH}

## Results

| Check | Status |
|-------|--------|
| Git repo | ✅ |
| Docker daemon | ✅ |
| TINVEST_TOKEN set | $([ "${TOKEN_SET}" = "true" ] && echo "✅" || echo "❌") |
| DataLoader init | $([ "${DATALOADER_STATUS}" != "error" ] && echo "✅" || echo "❌") |
| Candles loaded | $([ "${DATALOADER_STATUS}" = "success" ] && echo "✅ ${CANDLES_LOADED} candles" || echo "❌ ${CANDLES_LOADED:-0}") |

$([ "${STATUS}" != "success" ] && echo "## Error\n\n\`${DATALOADER_ERROR:-unknown}\`" || echo "")
EOF

echo ""
echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
