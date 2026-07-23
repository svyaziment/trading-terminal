#!/usr/bin/env bash
set -u

TASK_ID="task-032b-frontend-build-check"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

: > "${LOG_TXT}"

STATUS="success"
STAGE="done"

NODE_VERSION=""
NPM_VERSION=""
DIST_EXISTS=false
DIST_FILES_COUNT=0

BACKEND_STARTED="not_attempted"
API_HEALTH="unreachable"
API_SIGNALS="unreachable"
API_TOP="unreachable"
API_INSTRUMENTS="unreachable"

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"

write_report() {
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if [[ "${STATUS}" == "success" ]]; then
    NEXT_ACTION="Run: docker compose up -d backend, then cd frontend && npm run dev. Open http://localhost:5173"
  elif [[ "${STATUS}" == "needs_human" ]]; then
    NEXT_ACTION="Frontend build is OK, but backend/API is not ready. Start Docker Desktop / backend and rerun or start backend manually."
  else
    NEXT_ACTION="Check ${LOG_TXT}"
  fi

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "${STATUS}",
  "stage": "${STAGE}",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "node_version": "${NODE_VERSION}",
  "npm_version": "${NPM_VERSION}",
  "dist_exists": ${DIST_EXISTS},
  "dist_files_count": ${DIST_FILES_COUNT},
  "backend_started": "${BACKEND_STARTED}",
  "api_checks": {
    "health": "${API_HEALTH}",
    "signals": "${API_SIGNALS}",
    "top_stocks": "${API_TOP}",
    "instruments": "${API_INSTRUMENTS}"
  },
  "next_action": "${NEXT_ACTION}"
}
JSON

  cat > "${REPORT_MD}" <<MD
# ${TASK_ID}

Status: ${STATUS}
Stage: ${STAGE}
Started: ${STARTED_AT}
Finished: ${FINISHED_AT}

Node: ${NODE_VERSION}
NPM: ${NPM_VERSION}

dist exists: ${DIST_EXISTS}
dist files: ${DIST_FILES_COUNT}

Backend started: ${BACKEND_STARTED}

API checks:

- health: ${API_HEALTH}
- signals: ${API_SIGNALS}
- top-stocks: ${API_TOP}
- instruments: ${API_INSTRUMENTS}

Next action:

${NEXT_ACTION}
MD
}

if ! command -v node >/dev/null 2>&1; then
  log "ERROR: Node.js not found"
  STATUS="failed"
  STAGE="node_not_found"
  write_report
  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  log "ERROR: npm not found"
  STATUS="failed"
  STAGE="npm_not_found"
  write_report
  exit 0
fi

NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"

log "Node: ${NODE_VERSION}"
log "NPM: ${NPM_VERSION}"

if [[ ! -f frontend/package.json ]]; then
  log "ERROR: frontend/package.json not found"
  STATUS="failed"
  STAGE="frontend_missing"
  write_report
  exit 0
fi

if [[ ! -d frontend/node_modules ]]; then
  log "frontend/node_modules not found, running npm install"
  if ! (cd frontend && npm install --no-audit --no-fund --progress=false >> "${LOG_TXT}" 2>&1); then
    log "ERROR: npm install failed"
    STATUS="failed"
    STAGE="npm_install"
    write_report
    exit 0
  fi
fi

log "Running npm run build"
if ! (cd frontend && npm run build >> "${LOG_TXT}" 2>&1); then
  log "ERROR: npm run build failed"
  STATUS="failed"
  STAGE="npm_build"
  write_report
  exit 0
fi

if [[ -d frontend/dist ]]; then
  DIST_EXISTS=true
  DIST_FILES_COUNT=$(find frontend/dist -type f 2>/dev/null | wc -l | tr -d ' ')
  if [[ -z "${DIST_FILES_COUNT}" ]]; then
    DIST_FILES_COUNT=0
  fi
  log "frontend/dist exists, files: ${DIST_FILES_COUNT}"
else
  log "ERROR: frontend/dist not found after build"
  STATUS="failed"
  STAGE="dist_missing"
  write_report
  exit 0
fi

check_url() {
  local url="$1"
  local marker="$2"
  local body=""

  if command -v curl >/dev/null 2>&1; then
    body=$(curl -s -m 8 "${url}" 2>>"${LOG_TXT}" || echo "")
  else
    body=$(node -e "fetch(process.argv[1], { signal: AbortSignal.timeout(8000) }).then(function(r){return r.text();}).then(function(t){console.log(t);}).catch(function(){console.log('');});" "${url}" 2>>"${LOG_TXT}" || echo "")
  fi

  if echo "${body}" | grep -q "${marker}"; then
    echo "ok"
  else
    echo "unreachable"
  fi
}

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Trying to start backend container"
  BACKEND_STARTED="attempted"

  docker compose up -d --no-build backend >> "${LOG_TXT}" 2>&1 || true

  API_HEALTH="$(check_url http://localhost:8000/health status)"

  if [[ "${API_HEALTH}" != "ok" ]]; then
    log "Backend health not ready, trying docker compose build backend"
    docker compose build backend >> "${LOG_TXT}" 2>&1 || true
    docker compose up -d --no-build backend >> "${LOG_TXT}" 2>&1 || true
  fi

  for i in $(seq 1 20); do
    API_HEALTH="$(check_url http://localhost:8000/health status)"
    if [[ "${API_HEALTH}" == "ok" ]]; then
      log "Backend health is OK"
      break
    fi
    sleep 2
  done
else
  log "Docker Compose not available, checking API anyway"
  API_HEALTH="$(check_url http://localhost:8000/health status)"
fi

API_SIGNALS="$(check_url 'http://localhost:8000/api/signals?limit=1' items)"
API_TOP="$(check_url 'http://localhost:8000/api/top-stocks-by-volume?limit=1' items)"
API_INSTRUMENTS="$(check_url 'http://localhost:8000/api/instruments?limit=1' items)"

log "API health: ${API_HEALTH}"
log "API signals: ${API_SIGNALS}"
log "API top-stocks: ${API_TOP}"
log "API instruments: ${API_INSTRUMENTS}"

if [[ "${API_HEALTH}" != "ok" || "${API_SIGNALS}" != "ok" || "${API_TOP}" != "ok" || "${API_INSTRUMENTS}" != "ok" ]]; then
  STATUS="needs_human"
  STAGE="api_not_ready"
fi

write_report
log "Report JSON: ${REPORT_JSON}"
log "Status: ${STATUS}"
