#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-004-docker-build"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"

CHECKS_JSON=""
CHECKS_MD=""
STATUS="success"

ERRORS_JSON_ENTRIES=""
ERRORS_MD_LINES=""

add_check() {
  local name="$1"
  local path="$2"
  local ok="$3"
  local message="${4:-}"
  local severity="${5:-failed}"

  local check_status="failed"

  if [ "$ok" = "true" ]; then
    check_status="passed"
  fi

  if [ "$check_status" != "passed" ]; then
    if [ "$severity" = "needs_human" ]; then
      if [ "$STATUS" = "success" ]; then
        STATUS="needs_human"
      fi
    else
      STATUS="failed"
    fi
  fi

  local entry
  entry="$(printf '    {\n      "name": "%s",\n      "path": "%s",\n      "status": "%s",\n      "message": "%s"\n    }' "$name" "$path" "$check_status" "$message")"

  if [ -z "$CHECKS_JSON" ]; then
    CHECKS_JSON="$entry"
  else
    CHECKS_JSON="$CHECKS_JSON,
$entry"
  fi

  CHECKS_MD="$CHECKS_MD
- $check_status: $name \`$path\` $message"
}

add_error() {
  local message="$1"

  local entry
  entry="$(printf '    "%s"' "$message")"

  if [ -z "$ERRORS_JSON_ENTRIES" ]; then
    ERRORS_JSON_ENTRIES="$entry"
  else
    ERRORS_JSON_ENTRIES="$ERRORS_JSON_ENTRIES,
$entry"
  fi

  ERRORS_MD_LINES="$ERRORS_MD_LINES
- $message"
}

echo "Checking Docker daemon..."

if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  add_check "docker_daemon" "docker" "true"
  echo "OK: docker daemon is running"
else
  add_check "docker_daemon" "docker" "false" "Docker daemon is not running" "needs_human"
  add_error "Start Docker Desktop or Docker daemon, then rerun this script."
  echo "FAIL: docker daemon is not running"
fi

RUN_OUTPUT=""
PYTHON_VERSION=""
GIT_VERSION=""
OLLAMA_BASE_URL=""

if [ "$STATUS" = "success" ]; then
  echo "Building Docker image..."
  echo "This may take a few minutes on first run."

  if docker compose build agent; then
    add_check "docker_compose_build" "agent" "true"
    echo "OK: docker compose build succeeded"
  else
    add_check "docker_compose_build" "agent" "false" "docker compose build failed"
    add_error "Run manually: docker compose build agent"
    echo "FAIL: docker compose build failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Running container check..."

  if RUN_OUTPUT="$(docker compose run --rm -T agent bash -c 'python --version; git --version; echo "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-}"' 2>&1)"; then
    add_check "docker_run_check" "agent" "true"
    echo "OK: container run check succeeded"
    echo "Container output:"
    echo "$RUN_OUTPUT"
  else
    add_check "docker_run_check" "agent" "false" "docker compose run failed"
    add_error "Run manually: docker compose run --rm -T agent bash -c 'python --version; git --version'"
    echo "FAIL: container run check failed"
    echo "Container output:"
    echo "$RUN_OUTPUT" || true
  fi
fi

if [ -n "$RUN_OUTPUT" ]; then
  PYTHON_VERSION="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^Python ' | head -n1 | awk '{print $2}' || true)"
  GIT_VERSION="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^git version ' | head -n1 | awk '{print $3}' || true)"
  OLLAMA_BASE_URL="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^OLLAMA_BASE_URL=' | head -n1 | cut -d'=' -f2- || true)"
fi

if [ "$STATUS" = "success" ]; then
  if [ -n "$PYTHON_VERSION" ]; then
    add_check "python_version" "$PYTHON_VERSION" "true"
    echo "OK: python version detected: $PYTHON_VERSION"
  else
    add_check "python_version" "python" "false" "Python version not detected"
    echo "FAIL: python version not detected"
  fi

  if [ -n "$GIT_VERSION" ]; then
    add_check "git_version" "$GIT_VERSION" "true"
    echo "OK: git version detected: $GIT_VERSION"
  else
    add_check "git_version" "git" "false" "Git version not detected"
    echo "FAIL: git version not detected"
  fi

  if [ -n "$OLLAMA_BASE_URL" ]; then
    add_check "ollama_base_url" "$OLLAMA_BASE_URL" "true"
    echo "OK: OLLAMA_BASE_URL detected: $OLLAMA_BASE_URL"
  else
    add_check "ollama_base_url" "OLLAMA_BASE_URL" "false" "OLLAMA_BASE_URL is empty"
    echo "FAIL: OLLAMA_BASE_URL is empty"
  fi
fi

FINISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [ -z "$ERRORS_JSON_ENTRIES" ]; then
  ERRORS_JSON="[]"
else
  ERRORS_JSON="[
$ERRORS_JSON_ENTRIES
  ]"
fi

if [ -z "$ERRORS_MD_LINES" ]; then
  ERRORS_MD="No errors."
else
  ERRORS_MD="$ERRORS_MD_LINES"
fi

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "python_version": "$PYTHON_VERSION",
    "git_version": "$GIT_VERSION",
    "ollama_base_url": "$OLLAMA_BASE_URL"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "docker image",
    "container check output"
  ],
  "errors": $ERRORS_JSON,
  "log_file": "reports/$TASK_ID/log.txt"
}
EOF

cat > "$REPORT_MD" <<EOF
# Report $TASK_ID

Status: **$STATUS**

Started: $STARTED_AT  
Finished: $FINISHED_AT

Python version: **$PYTHON_VERSION**  
Git version: **$GIT_VERSION**  
OLLAMA_BASE_URL: **$OLLAMA_BASE_URL**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
