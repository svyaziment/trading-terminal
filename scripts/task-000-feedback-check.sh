#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-000-feedback-check"
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

echo "Creating base directories..."
mkdir -p docs
mkdir -p scripts
mkdir -p reports
mkdir -p logs

if [ ! -f "README.md" ]; then
  echo "Creating README.md..."
  cat > README.md <<'README_EOF'
# Trading Terminal

AI-assisted trading terminal for MOEX.

Stack:
- Python
- FastAPI
- PostgreSQL
- Redis
- React
- Tinkoff/T-Bank Invest API
README_EOF
else
  echo "README.md already exists, skipping."
fi

CHECKS_JSON=""
CHECKS_MD=""
STATUS="success"

add_check() {
  local name="$1"
  local path="$2"
  local ok="$3"
  local check_status="failed"

  if [ "$ok" = "true" ]; then
    check_status="passed"
  else
    STATUS="failed"
  fi

  local entry
  entry="$(printf '    {\n      "name": "%s",\n      "path": "%s",\n      "status": "%s",\n      "message": ""\n    }' "$name" "$path" "$check_status")"

  if [ -z "$CHECKS_JSON" ]; then
    CHECKS_JSON="$entry"
  else
    CHECKS_JSON="$CHECKS_JSON,
$entry"
  fi

  CHECKS_MD="$CHECKS_MD
- $check_status: $name \`$path\`"
}

check_dir() {
  local path="$1"
  if [ -d "$path" ]; then
    add_check "dir_exists" "$path" "true"
    echo "OK: dir exists: $path"
  else
    add_check "dir_exists" "$path" "false"
    echo "FAIL: dir missing: $path"
  fi
}

check_file() {
  local path="$1"
  if [ -f "$path" ]; then
    add_check "file_exists" "$path" "true"
    echo "OK: file exists: $path"
  else
    add_check "file_exists" "$path" "false"
    echo "FAIL: file missing: $path"
  fi
}

echo "Running checks..."

check_dir "docs"
check_dir "scripts"
check_dir "reports"
check_dir "logs"
check_file "README.md"

FINISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [ "$STATUS" = "success" ]; then
  ERRORS_JSON="[]"
  ERRORS_MD="No errors."
else
  ERRORS_JSON='[
    "One or more checks failed."
  ]'
  ERRORS_MD="- One or more checks failed."
fi

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "README.md",
    "docs",
    "scripts",
    "reports",
    "logs"
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

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
