#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-008-commit-agent-bootstrap"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

TARGET_BRANCH="chore/agent-bootstrap"
COMMIT_MESSAGE_TEXT="chore(task-008): add agent bootstrap scripts and Docker environment"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Target branch: $TARGET_BRANCH"

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

echo "Checking Git..."

GIT_BRANCH=""
GIT_IDENTITY_PRESENT="false"
COMMIT_SHA=""
COMMIT_MESSAGE=""
NO_CHANGES="false"
CHANGED_FILES_COUNT="0"

if command -v git >/dev/null 2>&1; then
  add_check "command_exists" "git" "true"
  echo "OK: git exists"
else
  add_check "command_exists" "git" "false" "Git command not found" "needs_human"
  add_error "Install Git, then rerun this script."
  echo "FAIL: git command not found"
fi

if [ -d ".git" ]; then
  add_check "git_repo" ".git" "true"
  echo "OK: git repository exists"
else
  add_check "git_repo" ".git" "false"
  add_error "Git repository missing. Run task-001-init-repo first."
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
    add_check "git_identity" "user.name,user.email" "true"
    echo "OK: git identity is configured"
  else
    GIT_IDENTITY_PRESENT="false"
    add_check "git_identity" "user.name,user.email" "false" "Git identity is not configured" "needs_human"
    add_error "Run: git config --global user.name 'Your Name' and git config --global user.email 'you@example.com'"
    echo "FAIL: git identity is not configured"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Staging files..."

  if ! git add -A; then
    STATUS="failed"
    add_check "git_add" "git add -A" "false"
    add_error "git add failed"
    echo "FAIL: git add failed"
  else
    add_check "git_add" "git add -A" "true"
    echo "OK: git add completed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if git diff --cached --quiet; then
    NO_CHANGES="true"
    CHANGED_FILES_COUNT="0"
    add_check "git_changes" "staged" "true" "No new changes to commit"
    echo "OK: no new changes to commit"
  else
    NO_CHANGES="false"
    CHANGED_FILES_COUNT="$(git diff --cached --name-only | wc -l | tr -d ' \r\n')"

    add_check "git_changes" "staged" "true" "Changes staged: $CHANGED_FILES_COUNT files"
    echo "OK: staged changes detected: $CHANGED_FILES_COUNT files"

    echo "Creating commit..."

    if ! git commit -m "$COMMIT_MESSAGE_TEXT"; then
      STATUS="failed"
      add_check "git_commit" "commit" "false"
      add_error "git commit failed"
      echo "FAIL: git commit failed"
    else
      add_check "git_commit" "commit" "true"
      echo "OK: git commit created"
    fi
  fi
fi

if [ "$STATUS" = "success" ]; then
  COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "")"
  COMMIT_MESSAGE="$(git log -1 --pretty=%B 2>/dev/null | tr -d '\r\n' || echo "")"

  if [ -n "$COMMIT_SHA" ]; then
    add_check "git_head_commit" "$COMMIT_SHA" "true"
    echo "OK: HEAD commit exists: $COMMIT_SHA"
  else
    add_check "git_head_commit" "HEAD" "false"
    add_error "HEAD commit missing"
    echo "FAIL: HEAD commit missing"
  fi

  if [ -z "$(git status --porcelain)" ]; then
    add_check "git_status_clean" "working tree" "true"
    echo "OK: working tree is clean"
  else
    add_check "git_status_clean" "working tree" "false" "Some files remain uncommitted or untracked" "needs_human"
    echo "WARNING: working tree is not clean"
    git status --short || true
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

COMMIT_MESSAGE_SAFE="$(printf '%s' "$COMMIT_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

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
    "no_changes": $NO_CHANGES,
    "changed_files_count": $CHANGED_FILES_COUNT,
    "git_commit_sha": "$COMMIT_SHA",
    "git_commit_message": "$COMMIT_MESSAGE_SAFE"
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
No changes: **$NO_CHANGES**  
Changed files count: **$CHANGED_FILES_COUNT**  
Commit SHA: **$COMMIT_SHA**  
Commit message: **$COMMIT_MESSAGE_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
