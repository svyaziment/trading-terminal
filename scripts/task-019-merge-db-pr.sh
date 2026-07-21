#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-019-merge-db-pr"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

PR_NUMBER="${PR_NUMBER:-5}"
EXECUTE_MODE="${EXECUTE_MODE:-dry-run}"
MAIN_BRANCH="main"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "PR number: $PR_NUMBER"
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

echo "Checking gh..."

GH_AVAILABLE="false"
GH_AUTH_OK="false"

if command -v gh >/dev/null 2>&1; then
  GH_AVAILABLE="true"
  add_check "command_exists" "gh" "true"
  echo "OK: gh exists"
else
  add_check "command_exists" "gh" "false" "GitHub CLI not found" "needs_human"
  add_error "Install GitHub CLI and rerun."
  echo "FAIL: gh not found"
fi

if [ "$GH_AVAILABLE" = "true" ]; then
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

echo "Checking working tree..."

if UNEXPECTED_CHANGES="$(working_tree_acceptable)"; then
  add_check "git_status_acceptable" "working tree" "true" "Clean or only untracked scripts"
  echo "OK: working tree is acceptable"
else
  add_check "git_status_acceptable" "working tree" "false" "Unexpected changes present" "needs_human"
  add_error "Unexpected changes in working tree. Commit or stash them before merging."
  echo "FAIL: unexpected changes in working tree"
  echo "$UNEXPECTED_CHANGES" || true
fi

PR_STATE=""
PR_IS_DRAFT=""
PR_MERGEABLE=""
PR_URL=""
PR_HEAD=""
PR_BASE=""
MERGED="false"
MAIN_SYNCED="false"

if [ "$STATUS" = "success" ]; then
  echo "Checking PR #$PR_NUMBER..."

  if PR_STATE="$(gh pr view "$PR_NUMBER" --json state --jq .state 2>/dev/null)"; then
    add_check "pr_exists" "#$PR_NUMBER" "true"
    echo "OK: PR #$PR_NUMBER exists"
  else
    PR_STATE=""
    add_check "pr_exists" "#$PR_NUMBER" "false" "PR not found" "needs_human"
    add_error "PR #$PR_NUMBER not found. Check PR number or repository."
    echo "FAIL: PR #$PR_NUMBER not found"
  fi
fi

if [ "$STATUS" = "success" ]; then
  PR_IS_DRAFT="$(gh pr view "$PR_NUMBER" --json isDraft --jq .isDraft 2>/dev/null || echo "")"
  PR_MERGEABLE="$(gh pr view "$PR_NUMBER" --json mergeable --jq .mergeable 2>/dev/null || echo "")"
  PR_URL="$(gh pr view "$PR_NUMBER" --json url --jq .url 2>/dev/null || echo "")"
  PR_HEAD="$(gh pr view "$PR_NUMBER" --json headRefName --jq .headRefName 2>/dev/null || echo "")"
  PR_BASE="$(gh pr view "$PR_NUMBER" --json baseRefName --jq .baseRefName 2>/dev/null || echo "")"

  echo "PR URL: $PR_URL"
  echo "PR state: $PR_STATE"
  echo "PR draft: $PR_IS_DRAFT"
  echo "PR mergeable: $PR_MERGEABLE"
  echo "PR head: $PR_HEAD"
  echo "PR base: $PR_BASE"

  add_check "pr_state" "$PR_STATE" "true"

  if [ "$PR_STATE" = "MERGED" ]; then
    MERGED="true"
    add_check "pr_already_merged" "#$PR_NUMBER" "true"
    echo "OK: PR already merged"
  elif [ "$PR_STATE" = "OPEN" ]; then
    if [ "$PR_IS_DRAFT" = "true" ]; then
      add_check "pr_draft" "#$PR_NUMBER" "true" "PR is draft"
      echo "OK: PR is draft"
    else
      add_check "pr_draft" "#$PR_NUMBER" "true" "PR is ready"
      echo "OK: PR is ready"
    fi

    if [ "$PR_MERGEABLE" = "CONFLICTING" ]; then
      add_check "pr_mergeable" "$PR_MERGEABLE" "false" "PR has conflicts" "needs_human"
      add_error "PR has conflicts. Resolve conflicts before merging."
      echo "FAIL: PR has conflicts"
    else
      add_check "pr_mergeable" "$PR_MERGEABLE" "true"
      echo "OK: PR mergeable state: $PR_MERGEABLE"
    fi
  elif [ "$PR_STATE" = "CLOSED" ]; then
    add_check "pr_state" "CLOSED" "false" "PR is closed" "needs_human"
    add_error "PR is closed. Restore it or create a new PR."
    echo "FAIL: PR is closed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$PR_STATE" = "OPEN" ]; then
  if [ "$EXECUTE_MODE" = "dry-run" ]; then
    STATUS="needs_human"
    add_check "dry_run" "EXECUTE_MODE" "false" "Dry-run only" "needs_human"
    add_error "Dry-run only. Run with EXECUTE_MODE=execute to merge PR."
    echo "DRY-RUN: no merge performed"
  else
    if [ "$PR_IS_DRAFT" = "true" ]; then
      echo "Marking PR as ready..."

      if gh pr ready "$PR_NUMBER"; then
        add_check "pr_ready" "#$PR_NUMBER" "true"
        echo "OK: PR marked as ready"
      else
        add_check "pr_ready" "#$PR_NUMBER" "false" "gh pr ready failed" "needs_human"
        add_error "gh pr ready failed. Check permissions."
        echo "FAIL: gh pr ready failed"
      fi
    fi
  fi
fi

if [ "$STATUS" = "success" ] && [ "$PR_STATE" = "OPEN" ] && [ "$EXECUTE_MODE" = "execute" ]; then
  echo "Merging PR with squash..."

  if gh pr merge "$PR_NUMBER" --squash; then
    MERGED="true"
    add_check "pr_merge" "#$PR_NUMBER" "true"
    echo "OK: PR merged"
  else
    add_check "pr_merge" "#$PR_NUMBER" "false" "gh pr merge failed" "needs_human"
    add_error "gh pr merge failed. Check branch protection or permissions."
    echo "FAIL: gh pr merge failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$MERGED" = "true" ]; then
  echo "Syncing local main..."

  if git fetch origin; then
    add_check "git_fetch" "origin" "true"
    echo "OK: git fetch completed"
  else
    add_check "git_fetch" "origin" "false" "git fetch failed" "needs_human"
    add_error "git fetch failed."
    echo "FAIL: git fetch failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$MERGED" = "true" ]; then
  if git checkout "$MAIN_BRANCH" 2>/dev/null; then
    add_check "git_checkout_main" "$MAIN_BRANCH" "true"
    echo "OK: checked out $MAIN_BRANCH"
  else
    if git checkout -b "$MAIN_BRANCH" "origin/$MAIN_BRANCH" 2>/dev/null; then
      add_check "git_checkout_main" "$MAIN_BRANCH" "true" "Created local main from origin/main"
      echo "OK: created local main from origin/main"
    else
      add_check "git_checkout_main" "$MAIN_BRANCH" "false" "Cannot checkout main" "needs_human"
      add_error "Cannot checkout main."
      echo "FAIL: cannot checkout main"
    fi
  fi
fi

if [ "$STATUS" = "success" ] && [ "$MERGED" = "true" ]; then
  if git pull --ff-only origin "$MAIN_BRANCH"; then
    MAIN_SYNCED="true"
    add_check "git_pull_main" "$MAIN_BRANCH" "true"
    echo "OK: main synced"
  else
    add_check "git_pull_main" "$MAIN_BRANCH" "false" "git pull failed" "needs_human"
    add_error "git pull --ff-only origin main failed."
    echo "FAIL: git pull failed"
  fi
fi

if [ "$STATUS" = "success" ] && [ "$MERGED" = "true" ]; then
  if UNEXPECTED_AFTER="$(working_tree_acceptable)"; then
    add_check "git_status_acceptable_after" "working tree" "true" "Clean or only untracked scripts"
    echo "OK: working tree is acceptable after sync"
  else
    add_check "git_status_acceptable_after" "working tree" "false" "Unexpected changes after sync" "needs_human"
    add_error "Unexpected changes after sync."
    echo "FAIL: unexpected changes after sync"
    echo "$UNEXPECTED_AFTER" || true
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
PR_HEAD_SAFE="$(printf '%s' "$PR_HEAD" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
PR_BASE_SAFE="$(printf '%s' "$PR_BASE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
PR_STATE_SAFE="$(printf '%s' "$PR_STATE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
PR_MERGEABLE_SAFE="$(printf '%s' "$PR_MERGEABLE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$PR_IS_DRAFT" != "true" ]; then
  PR_IS_DRAFT="false"
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
    "execute_mode": "$EXECUTE_MODE",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "pr_number": "$PR_NUMBER",
    "pr_url": "$PR_URL_SAFE",
    "pr_state": "$PR_STATE_SAFE",
    "pr_is_draft": $PR_IS_DRAFT,
    "pr_mergeable": "$PR_MERGEABLE_SAFE",
    "pr_head": "$PR_HEAD_SAFE",
    "pr_base": "$PR_BASE_SAFE",
    "merged": $MERGED,
    "main_synced": $MAIN_SYNCED
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "merged pull request",
    "local main"
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
Current branch: **$CURRENT_BRANCH_SAFE**  
PR number: **$PR_NUMBER**  
PR URL: **$PR_URL_SAFE**  
PR state: **$PR_STATE_SAFE**  
PR draft: **$PR_IS_DRAFT**  
PR mergeable: **$PR_MERGEABLE_SAFE**  
PR head: **$PR_HEAD_SAFE**  
PR base: **$PR_BASE_SAFE**  
Merged: **$MERGED**  
Main synced: **$MAIN_SYNCED**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
