#!/usr/bin/env bash
set -u

TASK_ID="task-032a-frontend-npm-diagnose"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PROJECT_ROOT="$(pwd)"
REPORT_DIR="${PROJECT_ROOT}/reports/${TASK_ID}"
mkdir -p "${REPORT_DIR}"

LOG_TXT="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"
NPM_LOG_ABS="${REPORT_DIR}/npm_install.log"
NPM_REGISTRY_LOG_ABS="${REPORT_DIR}/npm_install_registry.log"

: > "${LOG_TXT}"
: > "${NPM_LOG_ABS}"
: > "${NPM_REGISTRY_LOG_ABS}"

STATUS="failed"
STAGE="unknown"
PACKAGE_JSON_VALID="false"
NPM_EXIT=1
NPM_REGISTRY_EXIT=1
NPM_REGISTRY=""

log() {
  echo "$1" | tee -a "${LOG_TXT}"
}

log "Task: ${TASK_ID}"
log "Started: ${STARTED_AT}"
log "Project root: ${PROJECT_ROOT}"

if ! command -v node >/dev/null 2>&1; then
  log "ERROR: Node.js not found"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "node_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "Install Node.js LTS, preferably Node 20 or 22."
}
JSON

  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  log "ERROR: npm not found"

  cat > "${REPORT_JSON}" <<JSON
{
  "task_id": "${TASK_ID}",
  "status": "failed",
  "stage": "npm_not_found",
  "started_at": "${STARTED_AT}",
  "finished_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "next_action": "Install npm / Node.js LTS."
}
JSON

  exit 0
fi

NODE_VERSION="$(node --version)"
NPM_VERSION="$(npm --version)"

log "Node: ${NODE_VERSION}"
log "NPM: ${NPM_VERSION}"

NPM_REGISTRY="$(npm config get registry 2>>"${LOG_TXT}" || echo "")"
log "NPM registry: ${NPM_REGISTRY}"

if [[ ! -d frontend ]]; then
  STATUS="failed"
  STAGE="frontend_directory_not_found"
  log "ERROR: frontend directory not found"
fi

if [[ ! -f frontend/package.json ]]; then
  STATUS="failed"
  STAGE="package_json_not_found"
  log "ERROR: frontend/package.json not found"
fi

if [[ -f frontend/package.json ]]; then
  log "Validating frontend/package.json"
  if node -e "JSON.parse(require('fs').readFileSync('frontend/package.json','utf8'))" >>"${LOG_TXT}" 2>&1; then
    PACKAGE_JSON_VALID="true"
    log "package.json is valid JSON"
  else
    PACKAGE_JSON_VALID="false"
    STATUS="failed"
    STAGE="package_json_invalid"
    log "ERROR: package.json is not valid JSON"
  fi
fi

if [[ "${PACKAGE_JSON_VALID}" == "true" && -d frontend ]]; then
  log "Running npm cache verify"
  npm cache verify >>"${LOG_TXT}" 2>&1 || true

  log "Running npm install with verbose logging"
  (
    cd frontend &&
    npm install \
      --no-audit \
      --no-fund \
      --progress=false \
      --loglevel verbose \
      > "${NPM_LOG_ABS}" \
      2>&1
  )
  NPM_EXIT=$?
  log "npm install exit code: ${NPM_EXIT}"

  if [[ "${NPM_EXIT}" -ne 0 ]]; then
    log "Trying npm install with explicit public registry"
    (
      cd frontend &&
      npm install \
        --no-audit \
        --no-fund \
        --progress=false \
        --loglevel verbose \
        --registry=https://registry.npmjs.org/ \
        > "${NPM_REGISTRY_LOG_ABS}" \
        2>&1
    )
    NPM_REGISTRY_EXIT=$?
    log "npm install with public registry exit code: ${NPM_REGISTRY_EXIT}"
  else
    NPM_REGISTRY_EXIT=0
  fi

  if [[ "${NPM_EXIT}" -eq 0 || "${NPM_REGISTRY_EXIT}" -eq 0 ]]; then
    STATUS="success"
    STAGE="npm_install"
  else
    STATUS="failed"
    STAGE="npm_install"
  fi
fi

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

node - \
  "${REPORT_JSON}" \
  "${REPORT_MD}" \
  "${TASK_ID}" \
  "${STATUS}" \
  "${STAGE}" \
  "${STARTED_AT}" \
  "${FINISHED_AT}" \
  "${NODE_VERSION}" \
  "${NPM_VERSION}" \
  "${NPM_REGISTRY}" \
  "${PACKAGE_JSON_VALID}" \
  "${NPM_EXIT}" \
  "${NPM_REGISTRY_EXIT}" \
  "${NPM_LOG_ABS}" \
  "${NPM_REGISTRY_LOG_ABS}" <<'NODE_EOF'
const fs = require("fs");

const [
  reportPath,
  mdPath,
  taskId,
  status,
  stage,
  startedAt,
  finishedAt,
  nodeVersion,
  npmVersion,
  npmRegistry,
  packageValid,
  npmExit,
  registryExit,
  npmLogPath,
  registryLogPath,
] = process.argv.slice(2);

function tailFile(path, maxLines = 200) {
  try {
    const text = fs.readFileSync(path, "utf8");
    const lines = text.split(/\r?\n/);
    return lines.slice(-maxLines).join("\n");
  } catch (err) {
    return "";
  }
}

const npmTail = tailFile(npmLogPath, 200);
const registryTail = tailFile(registryLogPath, 200);

const report = {
  task_id: taskId,
  status,
  stage,
  started_at: startedAt,
  finished_at: finishedAt,
  node_version: nodeVersion,
  npm_version: npmVersion,
  npm_registry: npmRegistry,
  package_json_valid: packageValid === "true",
  npm_install_exit_code: Number(npmExit),
  npm_install_public_registry_exit_code: Number(registryExit),
  artifacts: {
    npm_install_log: npmLogPath,
    npm_install_registry_log: registryLogPath,
  },
  npm_tail: npmTail,
  npm_registry_tail: registryTail,
  next_action:
    status === "success"
      ? "Run cd frontend && npm run dev"
      : "Send report.json and the tail of npm_install.log",
};

fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");

const md = [
  "# " + taskId,
  "",
  "Status: " + status,
  "Stage: " + stage,
  "Node: " + nodeVersion,
  "NPM: " + npmVersion,
  "Registry: " + npmRegistry,
  "package.json valid: " + String(packageValid === "true"),
  "npm install exit code: " + npmExit,
  "npm install public registry exit code: " + registryExit,
  "",
  "## npm install tail",
  "",
  npmTail || "<empty>",
  "",
  "## npm install public registry tail",
  "",
  registryTail || "<empty>",
  "",
].join("\n");

fs.writeFileSync(mdPath, md, "utf8");
NODE_EOF

log "Report JSON: ${REPORT_JSON}"
log "Report MD: ${REPORT_MD}"
log "Status: ${STATUS}"
log "Done"
