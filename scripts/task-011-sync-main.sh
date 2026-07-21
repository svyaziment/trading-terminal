#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-011-sync-main"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

EXECUTE_MODE="${EXECUTE_MODE:-dry-run}"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Execute mode: $EXECUTE_MODE"

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

echo "Checking git..."

if command -v git >/dev/null 2>&1; then
  add_check "command_exists" "git" "true"
  echo "OK: git exists"
else
  add_check "command_exists" "git" "false" "Git not found" "needs_human"
  add_error "Install Git and rerun."
  echo "FAIL: git not found"
fi

if [ -d ".git" ]; then
  add_check "git_repo" ".git" "true"
  echo "OK: git repository exists"
else
  add_check "git_repo" ".git" "false"
  add_error "Git repository missing."
  echo "FAIL: git repository missing"
fi

CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
echo "Current branch: $CURRENT_BRANCH"

DIRTY_BEFORE="false"
STASH_CREATED="false"
STASH_MESSAGE="task-011-sync-main-$(date +%s)"
MAIN_SYNCED="false"
SCRIPT_BRANCH=""
COMMIT_SHA=""
COMMIT_CREATED="false"

if [ -n "$(git status --porcelain)" ]; then
  DIRTY_BEFORE="true"
  echo "Working tree is not clean."
  echo "Dirty files:"
  git status --short || true
else
  DIRTY_BEFORE="false"
  echo "Working tree is clean."
fi

if [ "$EXECUTE_MODE" = "dry-run" ]; then
  STATUS="needs_human"
  add_check "dry_run" "EXECUTE_MODE" "false" "Dry-run only" "needs_human"
  add_error "Dry-run only. Run with EXECUTE_MODE=execute to sync main."
  echo "DRY-RUN: no changes performed"
fi

if [ "$STATUS" = "success" ]; then
  echo "Fetching origin..."

  if git fetch origin; then
    add_check "git_fetch" "origin" "true"
    echo "OK: git fetch completed"
  else
    add_check "git_fetch" "origin" "false" "git fetch failed" "needs_human"
    add_error "git fetch failed."
    echo "FAIL: git fetch failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$DIRTY_BEFORE" = "true" ]; then
  echo "Stashing uncommitted and untracked files..."

  if git stash push -u -m "$STASH_MESSAGE"; then
    if git stash list | grep -q "$STASH_MESSAGE"; then
      STASH_CREATED="true"
      add_check "git_stash" "$STASH_MESSAGE" "true"
      echo "OK: stash created"
    else
      add_check "git_stash" "$STASH_MESSAGE" "false" "Stash message not found" "needs_human"
      add_error "Stash was created but message not found. Check git stash list."
      echo "FAIL: stash message not found"
    fi
  else
    add_check "git_stash" "$STASH_MESSAGE" "false" "git stash failed" "needs_human"
    add_error "git stash failed."
    echo "FAIL: git stash failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking out main..."

  if git checkout main 2>/dev/null; then
    add_check "git_checkout_main" "main" "true"
    echo "OK: checked out main"
  else
    if git checkout -b main origin/main 2>/dev/null; then
      add_check "git_checkout_main" "main" "true" "Created local main from origin/main"
      echo "OK: created local main from origin/main"
    else
      add_check "git_checkout_main" "main" "false" "Cannot checkout main" "needs_human"
      add_error "Cannot checkout main. Check origin/main exists."
      echo "FAIL: cannot checkout main"
    fi
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Pulling main..."

  if git pull --ff-only origin main; then
    MAIN_SYNCED="true"
    add_check "git_pull_main" "main" "true"
    echo "OK: main synced"
  else
    add_check "git_pull_main" "main" "false" "git pull failed" "needs_human"
    add_error "git pull --ff-only origin main failed."
    echo "FAIL: git pull failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$STASH_CREATED" = "true" ]; then
  SCRIPT_BRANCH_BASE="chore/task-scripts"
  SCRIPT_BRANCH="$SCRIPT_BRANCH_BASE"
  SUFFIX=2

  while git show-ref --verify --quiet "refs/heads/$SCRIPT_BRANCH"; do
    SCRIPT_BRANCH="${SCRIPT_BRANCH_BASE}-${SUFFIX}"
    SUFFIX=$((SUFFIX + 1))
  done

  echo "Creating branch for stashed task scripts: $SCRIPT_BRANCH"

  if git checkout -b "$SCRIPT_BRANCH" main; then
    add_check "git_checkout_scripts_branch" "$SCRIPT_BRANCH" "true"
    echo "OK: created branch $SCRIPT_BRANCH"
  else
    add_check "git_checkout_scripts_branch" "$SCRIPT_BRANCH" "false" "Cannot create scripts branch" "needs_human"
    add_error "Cannot create scripts branch."
    echo "FAIL: cannot create scripts branch"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$STASH_CREATED" = "true" ]; then
  echo "Popping stash..."

  if git stash pop; then
    add_check "git_stash_pop" "stash" "true"
    echo "OK: stash popped"
  else
    add_check "git_stash_pop" "stash" "false" "git stash pop failed" "needs_human"
    add_error "git stash pop failed. Check conflicts and git stash list."
    echo "FAIL: git stash pop failed"
    git status --short || true
  fi
fi

if [ "$STATUS" = "success" ] && [ "$STASH_CREATED" = "true" ]; then
  echo "Staging restored files..."

  if git add -A; then
    add_check "git_add" "git add -A" "true"
    echo "OK: git add completed"
  else
    add_check "git_add" "git add -A" "false"
    add_error "git add failed."
    echo "FAIL: git add failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$STASH_CREATED" = "true" ]; then
  if git diff --cached --quiet; then
    add_check "git_commit" "commit" "true" "No changes to commit after stash pop"
    echo "OK: no changes to commit after stash pop"
  else
    echo "Creating commit for restored task scripts..."

    if git commit -m "chore(task-011): add post-merge task scripts"; then
      COMMIT_CREATED="true"
      add_check "git_commit" "commit" "true"
      echo "OK: commit created"
    else
      add_check "git_commit" "commit" "false"
      add_error "git commit failed."
      echo "FAIL: git commit failed"
    fi
  fi
fi

if [ "$STATUS" = "success" ]; then
  COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo "")"

  if [ -n "$COMMIT_SHA" ]; then
    add_check "git_head_commit" "$COMMIT_SHA" "true"
    echo "OK: HEAD commit: $COMMIT_SHA"
  else
    add_check "git_head_commit" "HEAD" "false"
    add_error "HEAD commit missing."
    echo "FAIL: HEAD commit missing"
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

CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
SCRIPT_BRANCH_SAFE="$(printf '%s' "$SCRIPT_BRANCH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
COMMIT_SHA_SAFE="$(printf '%s' "$COMMIT_SHA" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
STASH_MESSAGE_SAFE="$(printf '%s' "$STASH_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "execute_mode": "$EXECUTE_MODE",
    "current_branch_before": "$CURRENT_BRANCH_SAFE",
    "dirty_before": $DIRTY_BEFORE,
    "stash_created": $STASH_CREATED,
    "stash_message": "$STASH_MESSAGE_SAFE",
    "main_synced": $MAIN_SYNCED,
    "scripts_branch": "$SCRIPT_BRANCH_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "local main",
    "scripts branch"
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

Execute mode: **$EXECUTE_MODE**  
Current branch before: **$CURRENT_BRANCH_SAFE**  
Dirty before: **$DIRTY_BEFORE**  
Stash created: **$STASH_CREATED**  
Stash message: **$STASH_MESSAGE_SAFE**  
Main synced: **$MAIN_SYNCED**  
Scripts branch: **$SCRIPT_BRANCH_SAFE**  
Commit created: **$COMMIT_CREATED**  
Commit SHA: **$COMMIT_SHA_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
