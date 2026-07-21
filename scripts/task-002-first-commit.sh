#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-002-first-commit"
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

echo "Checking Git..."

GIT_BRANCH=""
GIT_IDENTITY_PRESENT="false"
COMMIT_SHA=""
COMMIT_MESSAGE=""
TARGET_BRANCH="chore/agent-bootstrap"

if command -v git >/dev/null 2>&1; then
  add_check "command_exists" "git" "true"
  echo "OK: git exists"
else
  add_check "command_exists" "git" "false"
  echo "FAIL: git command not found"
fi

if [ -d ".git" ]; then
  add_check "git_repo" ".git" "true"
  echo "OK: git repository exists"
else
  add_check "git_repo" ".git" "false"
  echo "FAIL: git repository missing"
fi

if [ -d ".git" ]; then
  CURRENT_BRANCH_BEFORE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"

  if [ "$CURRENT_BRANCH_BEFORE" != "$TARGET_BRANCH" ]; then
    echo "Switching to branch: $TARGET_BRANCH"
    git checkout "$TARGET_BRANCH" 2>/dev/null || git checkout -b "$TARGET_BRANCH" || true
  else
    echo "Already on branch: $TARGET_BRANCH"
  fi

  GIT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"

  if [ "$GIT_BRANCH" = "$TARGET_BRANCH" ]; then
    add_check "git_branch" "$TARGET_BRANCH" "true"
    echo "OK: current branch is $TARGET_BRANCH"
  else
    add_check "git_branch" "$TARGET_BRANCH" "false"
    echo "FAIL: current branch is not $TARGET_BRANCH"
  fi

  GIT_USER_NAME="$(git config --get user.name || true)"
  GIT_USER_EMAIL="$(git config --get user.email || true)"

  if [ -n "$GIT_USER_NAME" ] && [ -n "$GIT_USER_EMAIL" ]; then
    GIT_IDENTITY_PRESENT="true"
    echo "OK: git identity is configured"
  else
    GIT_IDENTITY_PRESENT="false"
    echo "WARNING: git identity is not configured"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ "$GIT_IDENTITY_PRESENT" = "false" ]; then
    STATUS="needs_human"
    add_error "Git identity is not configured. Run: git config --global user.name 'Your Name' and git config --global user.email 'you@example.com', then rerun this script."
  else
    echo "Staging files..."

    if ! git add -A; then
      STATUS="failed"
      add_error "git add failed"
    fi
  fi
fi

if [ "$STATUS" = "success" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    echo "Creating commit..."

    if ! git commit -m "chore(task-002): add initial project structure"; then
      STATUS="failed"
      add_error "git commit failed"
    fi
  else
    echo "No changes to commit."
  fi
fi

if [ "$STATUS" = "success" ]; then
  COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "")"
  COMMIT_MESSAGE="$(git log -1 --pretty=%B 2>/dev/null | tr -d '\n' || echo "")"

  if [ -n "$COMMIT_SHA" ]; then
    add_check "git_commit" "$COMMIT_SHA" "true"
    echo "OK: commit exists: $COMMIT_SHA"
  else
    add_check "git_commit" "HEAD" "false"
    echo "FAIL: commit missing"
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
    "git_branch": "$GIT_BRANCH",
    "git_identity_present": $GIT_IDENTITY_PRESENT,
    "git_commit_sha": "$COMMIT_SHA",
    "git_commit_message": "$COMMIT_MESSAGE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "git commit"
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

Git branch: **$GIT_BRANCH**  
Git identity present: **$GIT_IDENTITY_PRESENT**  
Commit SHA: **$COMMIT_SHA**  
Commit message: **$COMMIT_MESSAGE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
