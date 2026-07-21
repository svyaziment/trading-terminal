#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-012a-backend-branch-fix"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

MAIN_BRANCH="main"
FEATURE_BRANCH="feat/backend-base"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
echo "Main branch: $MAIN_BRANCH"
echo "Feature branch: $FEATURE_BRANCH"

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

CURRENT_BRANCH_BEFORE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
echo "Current branch before: $CURRENT_BRANCH_BEFORE"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean."
  echo "Current changes:"
  git status --short || true
else
  echo "Working tree is clean."
fi

if [ "$STATUS" = "success" ]; then
  if [ "$CURRENT_BRANCH_BEFORE" = "$MAIN_BRANCH" ]; then
    echo "Moving current changes from main to $FEATURE_BRANCH..."

    if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
      if git checkout "$FEATURE_BRANCH"; then
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Branch already exists"
        echo "OK: checked out existing $FEATURE_BRANCH"
      else
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot checkout existing feature branch" "needs_human"
        add_error "Cannot checkout existing feature branch. Resolve conflicts or stash changes."
        echo "FAIL: cannot checkout existing feature branch"
      fi
    else
      if git checkout -b "$FEATURE_BRANCH"; then
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Created from current main with local changes"
        echo "OK: created $FEATURE_BRANCH from current state"
      else
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot create feature branch"
        add_error "Cannot create feature branch."
        echo "FAIL: cannot create feature branch"
      fi
    fi
  elif [ "$CURRENT_BRANCH_BEFORE" = "$FEATURE_BRANCH" ]; then
    add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Already on feature branch"
    echo "OK: already on $FEATURE_BRANCH"
  else
    add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Current branch is not main or feature branch" "needs_human"
    add_error "Current branch is not main or $FEATURE_BRANCH. Checkout one of them and rerun."
    echo "FAIL: unexpected current branch: $CURRENT_BRANCH_BEFORE"
  fi
fi

CURRENT_BRANCH_AFTER="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
echo "Current branch after: $CURRENT_BRANCH_AFTER"

if [ "$STATUS" = "success" ]; then
  if [ "$CURRENT_BRANCH_AFTER" = "$FEATURE_BRANCH" ]; then
    add_check "git_branch_after" "$FEATURE_BRANCH" "true"
    echo "OK: current branch is $FEATURE_BRANCH"
  else
    add_check "git_branch_after" "$FEATURE_BRANCH" "false"
    add_error "Did not switch to $FEATURE_BRANCH."
    echo "FAIL: current branch is not $FEATURE_BRANCH"
  fi
fi

echo "Checking backend files..."

for f in \
  backend/app/__init__.py \
  backend/app/main.py \
  backend/tests/__init__.py \
  backend/tests/test_health.py \
  backend/requirements.txt \
  backend/requirements-dev.txt \
  backend/Dockerfile \
  backend/.dockerignore \
  backend/pyproject.toml \
  docker-compose.yml
do
  if [ -f "$f" ]; then
    add_check "file_exists" "$f" "true"
    echo "OK: file exists: $f"
  else
    add_check "file_exists" "$f" "false"
    echo "FAIL: file missing: $f"
  fi
done

echo "Checking Docker daemon..."

if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  add_check "docker_daemon" "docker" "true"
  echo "OK: docker daemon is running"
else
  add_check "docker_daemon" "docker" "false" "Docker daemon is not running" "needs_human"
  add_error "Start Docker Desktop or Docker daemon, then rerun this script."
  echo "FAIL: docker daemon is not running"
fi

if [ "$STATUS" = "success" ]; then
  echo "Validating docker-compose.yml..."

  if docker compose config -q >/dev/null 2>&1; then
    add_check "compose_config" "docker-compose.yml" "true"
    echo "OK: docker-compose.yml is valid"
  else
    add_check "compose_config" "docker-compose.yml" "false"
    add_error "Run manually: docker compose config"
    echo "FAIL: docker-compose.yml validation failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Building backend image..."
  echo "This may take a few minutes."

  if docker compose build backend; then
    add_check "docker_compose_build_backend" "backend" "true"
    echo "OK: backend image built"
  else
    add_check "docker_compose_build_backend" "backend" "false"
    add_error "Run manually: docker compose build backend"
    echo "FAIL: backend image build failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Running backend tests..."

  if docker compose run --rm -T backend pytest -q; then
    add_check "backend_tests" "pytest" "true"
    echo "OK: backend tests passed"
  else
    add_check "backend_tests" "pytest" "false"
    add_error "Run manually: docker compose run --rm -T backend pytest -q"
    echo "FAIL: backend tests failed"
  fi
fi

COMMIT_CREATED="false"
COMMIT_SHA=""

if [ "$STATUS" = "success" ]; then
  echo "Staging files..."

  if git add -A; then
    add_check "git_add" "git add -A" "true"
    echo "OK: git add completed"
  else
    add_check "git_add" "git add -A" "false"
    add_error "git add failed."
    echo "FAIL: git add failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  if git diff --cached --quiet; then
    add_check "git_commit" "commit" "true" "No changes to commit"
    echo "OK: no changes to commit"
  else
    echo "Creating commit..."

    if git commit -m "feat(task-012): add backend base with health endpoint"; then
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

  if [ -z "$(git status --porcelain)" ]; then
    add_check "git_status_clean" "working tree" "true"
    echo "OK: working tree is clean"
  else
    add_check "git_status_clean" "working tree" "false" "Working tree is not clean after commit" "needs_human"
    echo "WARNING: working tree is not clean after commit"
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

CURRENT_BRANCH_BEFORE_SAFE="$(printf '%s' "$CURRENT_BRANCH_BEFORE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
CURRENT_BRANCH_AFTER_SAFE="$(printf '%s' "$CURRENT_BRANCH_AFTER" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
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
    "main_branch": "$MAIN_BRANCH",
    "feature_branch": "$FEATURE_BRANCH",
    "current_branch_before": "$CURRENT_BRANCH_BEFORE_SAFE",
    "current_branch_after": "$CURRENT_BRANCH_AFTER_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/main.py",
    "backend/tests/test_health.py",
    "backend/Dockerfile",
    "docker-compose.yml",
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

Main branch: **$MAIN_BRANCH**  
Feature branch: **$FEATURE_BRANCH**  
Current branch before: **$CURRENT_BRANCH_BEFORE_SAFE**  
Current branch after: **$CURRENT_BRANCH_AFTER_SAFE**  
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
