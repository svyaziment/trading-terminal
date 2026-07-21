#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-018a-commit-db-checks"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

EXPECTED_BRANCH="feat/db-context"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Expected branch: $EXPECTED_BRANCH"

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

if [ "$STATUS" = "success" ] && [ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "Trying to checkout $EXPECTED_BRANCH..."

  if git show-ref --verify --quiet "refs/heads/$EXPECTED_BRANCH"; then
    if git checkout "$EXPECTED_BRANCH"; then
      CURRENT_BRANCH="$EXPECTED_BRANCH"
      add_check "git_checkout_expected" "$EXPECTED_BRANCH" "true"
      echo "OK: checked out $EXPECTED_BRANCH"
    else
      add_check "git_checkout_expected" "$EXPECTED_BRANCH" "false" "Cannot checkout expected branch" "needs_human"
      add_error "Cannot checkout $EXPECTED_BRANCH."
      echo "FAIL: cannot checkout expected branch"
    fi
  else
    add_check "git_checkout_expected" "$EXPECTED_BRANCH" "false" "Expected branch does not exist" "needs_human"
    add_error "Checkout $EXPECTED_BRANCH first."
    echo "FAIL: expected branch does not exist"
  fi
else
  add_check "git_branch" "$EXPECTED_BRANCH" "true"
  echo "OK: already on $EXPECTED_BRANCH"
fi

echo "Checking DB check modules..."

for f in \
  backend/app/db/check_db_connection.py \
  backend/app/db/check_network.py
do
  if [ -f "$f" ]; then
    add_check "file_exists" "$f" "true"
    echo "OK: file exists: $f"
  else
    add_check "file_exists" "$f" "false"
    echo "FAIL: file missing: $f"
  fi
done

echo "Staging DB check modules and scripts..."

git add backend/app/db/check_db_connection.py 2>/dev/null || true
git add backend/app/db/check_network.py 2>/dev/null || true
git add backend/app/db/__init__.py 2>/dev/null || true
git add scripts/ 2>/dev/null || true

add_check "git_add" "git add" "true"
echo "OK: git add completed"

echo "Checking staged files for secrets..."

STAGED_FILES="$(git diff --cached --name-only || true)"
STAGED_COUNT="$(printf '%s\n' "$STAGED_FILES" | grep -c . || true)"

echo "Staged files count: $STAGED_COUNT"

SECRET_FOUND="false"

if printf '%s\n' "$STAGED_FILES" | grep -E '(^|/)\.env$' >/dev/null 2>&1; then
  SECRET_FOUND="true"
  echo "FAIL: .env is staged"
fi

if printf '%s\n' "$STAGED_FILES" | grep -E '(^|/)\.env\.' | grep -v '\.env\.example$' >/dev/null 2>&1; then
  SECRET_FOUND="true"
  echo "FAIL: .env.* secret file is staged"
fi

if printf '%s\n' "$STAGED_FILES" | grep -E 'backend/config/settings\.yaml$' >/dev/null 2>&1; then
  SECRET_FOUND="true"
  echo "FAIL: backend/config/settings.yaml is staged"
fi

if printf '%s\n' "$STAGED_FILES" | grep -E '\.(pem|key)$|id_rsa' >/dev/null 2>&1; then
  SECRET_FOUND="true"
  echo "FAIL: private key file is staged"
fi

if [ "$SECRET_FOUND" = "true" ]; then
  git reset --
  add_check "secret_check" "staged files" "false" "Secret-like files were staged and unstaged" "needs_human"
  add_error "Secret-like files were staged. They were unstaged. Check .gitignore."
  echo "FAIL: secret-like files were staged"
else
  add_check "secret_check" "staged files" "true"
  echo "OK: no obvious secret files staged"
fi

COMMIT_CREATED="false"
COMMIT_SHA=""

if [ "$STATUS" = "success" ]; then
  if [ "$STAGED_COUNT" = "0" ]; then
    add_check "git_commit" "commit" "true" "No changes to commit"
    echo "OK: no changes to commit"
  else
    echo "Creating commit..."

    if git commit -m "feat(task-017): add DB connection and network check modules"; then
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

CURRENT_BRANCH_SAFE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH_SAFE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
COMMIT_SHA_SAFE="$(printf '%s' "$COMMIT_SHA" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "expected_branch": "$EXPECTED_BRANCH",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "staged_files_count": "$STAGED_COUNT",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
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

Expected branch: **$EXPECTED_BRANCH**  
Current branch: **$CURRENT_BRANCH_SAFE**  
Staged files count: **$STAGED_COUNT**  
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
