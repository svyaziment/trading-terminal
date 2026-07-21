#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-018-push-db-pr"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

FEATURE_BRANCH="feat/db-context"
BASE_BRANCH="main"

PR_TITLE="feat: database context, config manager and DB connection checks"
PR_BODY="Add database architecture docs, data model docs, backend config, sync DBManager, PostgreSQL network check and DB connection check.

- docs/architecture/database.md
- docs/domain/data-model.md
- backend/app/core/config.py
- backend/app/core/config_manager.py
- backend/app/db/db_manager.py
- backend/app/db/check_network.py
- backend/app/db/check_db_connection.py
- backend/config/settings.yaml.example
- backend tests
- .env.example updates
- docker-compose backend environment updates"

PR_DRAFT="${PR_DRAFT:-false}"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Feature branch: $FEATURE_BRANCH"
echo "Base branch: $BASE_BRANCH"
echo "PR draft: $PR_DRAFT"

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

working_tree_acceptable() {
  local porcelain
  porcelain="$(git status --porcelain)"

  if [ -z "$porcelain" ]; then
    return 0
  fi

  local unexpected
  unexpected="$(printf '%s\n' "$porcelain" | grep -v -E '^\?\? scripts/' || true)"

  if [ -n "$unexpected" ]; then
    echo "$unexpected"
    return 1
  fi

  return 0
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

if [ "$STATUS" = "success" ] && [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
  echo "Trying to checkout $FEATURE_BRANCH..."

  if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
    if git checkout "$FEATURE_BRANCH"; then
      CURRENT_BRANCH="$FEATURE_BRANCH"
      add_check "git_branch" "$FEATURE_BRANCH" "true" "Switched to existing branch"
      echo "OK: switched to $FEATURE_BRANCH"
    else
      add_check "git_branch" "$FEATURE_BRANCH" "false" "Cannot checkout feature branch" "needs_human"
      add_error "Cannot checkout $FEATURE_BRANCH."
      echo "FAIL: cannot checkout feature branch"
    fi
  else
    add_check "git_branch" "$FEATURE_BRANCH" "false" "Feature branch does not exist" "needs_human"
    add_error "Run database tasks first or checkout $FEATURE_BRANCH."
    echo "FAIL: feature branch does not exist"
  fi
else
  add_check "git_branch" "$FEATURE_BRANCH" "true"
  echo "OK: already on $FEATURE_BRANCH"
fi

echo "Checking working tree..."

if UNEXPECTED_CHANGES="$(working_tree_acceptable)"; then
  add_check "git_status_acceptable" "working tree" "true" "Clean or only untracked scripts"
  echo "OK: working tree is acceptable"
else
  add_check "git_status_acceptable" "working tree" "false" "Unexpected changes present" "needs_human"
  add_error "Unexpected changes in working tree. Commit or stash them before pushing."
  echo "FAIL: unexpected changes in working tree"
  echo "$UNEXPECTED_CHANGES" || true
fi

echo "Checking remote origin..."

REMOTE_URL=""
REMOTE_URL_SAFE=""

if [ "$STATUS" = "success" ]; then
  if REMOTE_URL="$(git remote get-url origin 2>/dev/null)"; then
    add_check "git_remote_origin" "origin" "true"
    echo "OK: remote origin exists"
  else
    REMOTE_URL=""
    add_check "git_remote_origin" "origin" "false" "Remote origin not found" "needs_human"
    add_error "Add remote origin first."
    echo "FAIL: remote origin not found"
  fi

  REMOTE_URL_SAFE="$(printf '%s' "$REMOTE_URL" | sed -E 's#(https?|git|ssh)://[^@/]+@#\1://#g' | tr -d '\r' | tr -d '"' | tr -d '\\')"
fi

echo "Checking gh..."

GH_AVAILABLE="false"
GH_AUTH_OK="false"

if [ "$STATUS" = "success" ]; then
  if command -v gh >/dev/null 2>&1; then
    GH_AVAILABLE="true"
    add_check "command_exists" "gh" "true"
    echo "OK: gh exists"
  else
    add_check "command_exists" "gh" "false" "GitHub CLI not found" "needs_human"
    add_error "Install GitHub CLI and rerun."
    echo "FAIL: gh not found"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$GH_AVAILABLE" = "true" ]; then
  if gh auth status >/dev/null 2>&1; then
    GH_AUTH_OK="true"
    add_check "gh_auth" "gh" "true"
    echo "OK: gh auth configured"
  else
    add_check "gh_auth" "gh" "false" "gh auth failed" "needs_human"
    add_error "Run: gh auth login"
    echo "FAIL: gh auth failed"
  fi
fi

PUSH_OK="false"
REMOTE_BRANCH_OK="false"

if [ "$STATUS" = "success" ]; then
  echo "Pushing feature branch..."

  if git push -u origin "$FEATURE_BRANCH"; then
    PUSH_OK="true"
    add_check "git_push" "$FEATURE_BRANCH" "true"
    echo "OK: branch pushed"
  else
    add_check "git_push" "$FEATURE_BRANCH" "false" "git push failed" "needs_human"
    add_error "Run manually: git push -u origin $FEATURE_BRANCH"
    echo "FAIL: git push failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking remote branch..."

  if git ls-remote --heads origin "$FEATURE_BRANCH" | grep -q "refs/heads/$FEATURE_BRANCH"; then
    REMOTE_BRANCH_OK="true"
    add_check "remote_branch" "$FEATURE_BRANCH" "true"
    echo "OK: remote branch exists"
  else
    add_check "remote_branch" "$FEATURE_BRANCH" "false" "Remote branch not found after push" "needs_human"
    add_error "Remote branch not found after push."
    echo "FAIL: remote branch not found"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking base branch..."

  if git ls-remote --heads origin "$BASE_BRANCH" | grep -q "refs/heads/$BASE_BRANCH"; then
    add_check "base_branch" "$BASE_BRANCH" "true"
    echo "OK: base branch exists: $BASE_BRANCH"
  else
    add_check "base_branch" "$BASE_BRANCH" "false" "Base branch not found" "needs_human"
    add_error "Base branch $BASE_BRANCH not found on origin."
    echo "FAIL: base branch not found"
  fi
fi

PR_URL=""
PR_CREATED="false"
PR_EXISTS="false"

if [ "$STATUS" = "success" ] && [ "$GH_AUTH_OK" = "true" ]; then
  echo "Checking existing pull request..."

  PR_URL="$(gh pr list --head "$FEATURE_BRANCH" --json url --jq '.[0].url' 2>/dev/null || true)"

  if [ -n "$PR_URL" ]; then
    PR_EXISTS="true"
    add_check "pr_exists" "$PR_URL" "true"
    echo "OK: pull request already exists: $PR_URL"
  else
    echo "Creating pull request..."

    if [ "$PR_DRAFT" = "true" ]; then
      if PR_CREATE_OUTPUT="$(gh pr create \
          --draft \
          --title "$PR_TITLE" \
          --body "$PR_BODY" \
          --base "$BASE_BRANCH" \
          --head "$FEATURE_BRANCH" 2>&1)"; then
        PR_URL="$PR_CREATE_OUTPUT"
        PR_CREATED="true"
        PR_EXISTS="true"
        add_check "pr_create" "$PR_URL" "true"
        echo "OK: draft pull request created: $PR_URL"
      else
        PR_CREATE_OUTPUT_SAFE="$(printf '%s' "$PR_CREATE_OUTPUT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
        add_check "pr_create" "gh pr create" "false" "gh pr create failed" "needs_human"
        add_error "gh pr create failed: $PR_CREATE_OUTPUT_SAFE"
        echo "FAIL: gh pr create failed"
        echo "$PR_CREATE_OUTPUT" || true
      fi
    else
      if PR_CREATE_OUTPUT="$(gh pr create \
          --title "$PR_TITLE" \
          --body "$PR_BODY" \
          --base "$BASE_BRANCH" \
          --head "$FEATURE_BRANCH" 2>&1)"; then
        PR_URL="$PR_CREATE_OUTPUT"
        PR_CREATED="true"
        PR_EXISTS="true"
        add_check "pr_create" "$PR_URL" "true"
        echo "OK: pull request created: $PR_URL"
      else
        PR_CREATE_OUTPUT_SAFE="$(printf '%s' "$PR_CREATE_OUTPUT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
        add_check "pr_create" "gh pr create" "false" "gh pr create failed" "needs_human"
        add_error "gh pr create failed: $PR_CREATE_OUTPUT_SAFE"
        echo "FAIL: gh pr create failed"
        echo "$PR_CREATE_OUTPUT" || true
      fi
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

CURRENT_BRANCH_SAFE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH_SAFE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
PR_URL_SAFE="$(printf '%s' "$PR_URL" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "feature_branch": "$FEATURE_BRANCH",
    "base_branch": "$BASE_BRANCH",
    "remote_url": "$REMOTE_URL_SAFE",
    "push_ok": $PUSH_OK,
    "remote_branch_ok": $REMOTE_BRANCH_OK,
    "gh_available": $GH_AVAILABLE,
    "gh_auth_ok": $GH_AUTH_OK,
    "pr_draft": $PR_DRAFT,
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

Current branch: **$CURRENT_BRANCH_SAFE**  
Feature branch: **$FEATURE_BRANCH**  
Base branch: **$BASE_BRANCH**  
Remote URL: **$REMOTE_URL_SAFE**  
Push OK: **$PUSH_OK**  
Remote branch OK: **$REMOTE_BRANCH_OK**  
GitHub CLI available: **$GH_AVAILABLE**  
GitHub auth OK: **$GH_AUTH_OK**  
PR draft: **$PR_DRAFT**  
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
