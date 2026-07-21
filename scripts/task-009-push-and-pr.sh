#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-009-push-and-pr"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

TARGET_BRANCH="chore/agent-bootstrap"
BASE_BRANCH="${BASE_BRANCH:-}"

PR_TITLE="chore: agent bootstrap"
PR_BODY="Add initial agent bootstrap: project structure, Docker environment, Ollama checks, orchestrator prompt, DevOps executor and first orchestrator-generated artifact."

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
REMOTE_URL=""
REMOTE_URL_SAFE=""
PUSH_OK="false"
REMOTE_BRANCH_OK="false"
GH_AVAILABLE="false"
GH_AUTH_OK="false"
PR_URL=""
PR_CREATED="false"
PR_EXISTS="false"

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
  add_error "Git repository missing."
  echo "FAIL: git repository missing"
fi

if [ -d ".git" ]; then
  GIT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"

  if [ "$GIT_BRANCH" = "$TARGET_BRANCH" ]; then
    add_check "git_branch" "$TARGET_BRANCH" "true"
    echo "OK: current branch is $TARGET_BRANCH"
  else
    add_check "git_branch" "$TARGET_BRANCH" "false"
    add_error "Current branch is not $TARGET_BRANCH. Checkout $TARGET_BRANCH and rerun."
    echo "FAIL: current branch is not $TARGET_BRANCH"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking remote origin..."

  if REMOTE_URL="$(git remote get-url origin 2>/dev/null)"; then
    add_check "git_remote_origin" "origin" "true"
    echo "OK: remote origin exists"
  else
    REMOTE_URL=""
    add_check "git_remote_origin" "origin" "false" "Remote origin not found" "needs_human"
    add_error "Add remote origin. Example: git remote add origin git@github.com:USERNAME/trading-terminal.git"
    echo "FAIL: remote origin not found"
  fi

  REMOTE_URL_SAFE="$(printf '%s' "$REMOTE_URL" | sed -E 's#(https?|git|ssh)://[^@/]+@#\1://#g' | tr -d '\r' | tr -d '"' | tr -d '\\')"
fi

if [ "$STATUS" = "success" ]; then
  echo "Pushing branch to origin..."

  if git push -u origin "$TARGET_BRANCH"; then
    PUSH_OK="true"
    add_check "git_push" "$TARGET_BRANCH" "true"
    echo "OK: branch pushed"
  else
    PUSH_OK="false"
    add_check "git_push" "$TARGET_BRANCH" "false" "git push failed" "needs_human"
    add_error "Run manually: git push -u origin $TARGET_BRANCH"
    echo "FAIL: git push failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking remote branch..."

  if git ls-remote --heads origin "$TARGET_BRANCH" | grep -q "refs/heads/$TARGET_BRANCH"; then
    REMOTE_BRANCH_OK="true"
    add_check "remote_branch" "$TARGET_BRANCH" "true"
    echo "OK: remote branch exists"
  else
    REMOTE_BRANCH_OK="false"
    add_check "remote_branch" "$TARGET_BRANCH" "false" "Remote branch not found after push" "needs_human"
    add_error "Remote branch not found after push. Check remote permissions and network."
    echo "FAIL: remote branch not found"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking GitHub CLI..."

  if command -v gh >/dev/null 2>&1; then
    GH_AVAILABLE="true"
    add_check "command_exists" "gh" "true"
    echo "OK: gh exists"
  else
    GH_AVAILABLE="false"
    add_check "command_exists" "gh" "false" "GitHub CLI not found" "needs_human"
    add_error "Install GitHub CLI: https://cli.github.com/"
    echo "FAIL: gh not found"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$GH_AVAILABLE" = "true" ]; then
  echo "Checking gh auth..."

  if gh auth status >/dev/null 2>&1; then
    GH_AUTH_OK="true"
    add_check "gh_auth" "gh" "true"
    echo "OK: gh auth is configured"
  else
    GH_AUTH_OK="false"
    add_check "gh_auth" "gh" "false" "gh auth failed" "needs_human"
    add_error "Run: gh auth login"
    echo "FAIL: gh auth is not configured"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$GH_AUTH_OK" = "true" ]; then
  echo "Detecting base branch..."

  if [ -z "$BASE_BRANCH" ]; then
    BASE_BRANCH="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || true)"
  fi

  if [ -z "$BASE_BRANCH" ]; then
    if git ls-remote --heads origin main | grep -q "refs/heads/main"; then
      BASE_BRANCH="main"
    elif git ls-remote --heads origin master | grep -q "refs/heads/master"; then
      BASE_BRANCH="master"
    fi
  fi

  if [ -n "$BASE_BRANCH" ]; then
    add_check "base_branch" "$BASE_BRANCH" "true"
    echo "OK: base branch detected: $BASE_BRANCH"
  else
    add_check "base_branch" "main/master" "false" "Base branch not found" "needs_human"
    add_error "Cannot detect base branch. Create main branch or set BASE_BRANCH env variable."
    echo "FAIL: base branch not found"
  fi

  if [ "$BASE_BRANCH" = "$TARGET_BRANCH" ]; then
    add_check "base_branch" "$BASE_BRANCH" "false" "Base branch equals target branch" "needs_human"
    add_error "Base branch must not equal target branch. Create main branch first."
    echo "FAIL: base branch equals target branch"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$GH_AUTH_OK" = "true" ] && [ -n "$BASE_BRANCH" ] && [ "$BASE_BRANCH" != "$TARGET_BRANCH" ]; then
  echo "Checking existing pull request..."

  if PR_URL="$(gh pr view "$TARGET_BRANCH" --json url --jq .url 2>/dev/null)"; then
    PR_EXISTS="true"
    PR_CREATED="false"
    add_check "pr_exists" "$PR_URL" "true"
    echo "OK: pull request already exists: $PR_URL"
  else
    echo "Creating draft pull request..."

    if PR_CREATE_OUTPUT="$(gh pr create \
        --title "$PR_TITLE" \
        --body "$PR_BODY" \
        --base "$BASE_BRANCH" \
        --head "$TARGET_BRANCH" \
        --draft 2>&1)"; then
      PR_URL="$PR_CREATE_OUTPUT"
      PR_CREATED="true"
      PR_EXISTS="true"
      add_check "pr_create" "$PR_URL" "true"
      echo "OK: pull request created: $PR_URL"
    else
      PR_CREATE_OUTPUT_SAFE="$(printf '%s' "$PR_CREATE_OUTPUT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
      PR_URL=""
      PR_CREATED="false"
      add_check "pr_create" "gh pr create" "false" "gh pr create failed" "needs_human"
      add_error "gh pr create failed: $PR_CREATE_OUTPUT_SAFE"
      echo "FAIL: gh pr create failed"
      echo "$PR_CREATE_OUTPUT" || true
    fi
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

PR_URL_SAFE="$(printf '%s' "$PR_URL" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
BASE_BRANCH_SAFE="$(printf '%s' "$BASE_BRANCH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

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
    "remote_url": "$REMOTE_URL_SAFE",
    "push_ok": $PUSH_OK,
    "remote_branch_ok": $REMOTE_BRANCH_OK,
    "gh_available": $GH_AVAILABLE,
    "gh_auth_ok": $GH_AUTH_OK,
    "base_branch": "$BASE_BRANCH_SAFE",
    "pr_exists": $PR_EXISTS,
    "pr_created": $PR_CREATED,
    "pr_url": "$PR_URL_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "remote branch",
    "pull request"
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
Remote URL: **$REMOTE_URL_SAFE**  
Push OK: **$PUSH_OK**  
Remote branch OK: **$REMOTE_BRANCH_OK**  
GitHub CLI available: **$GH_AVAILABLE**  
GitHub auth OK: **$GH_AUTH_OK**  
Base branch: **$BASE_BRANCH_SAFE**  
PR exists: **$PR_EXISTS**  
PR created: **$PR_CREATED**  
PR URL: **$PR_URL_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
