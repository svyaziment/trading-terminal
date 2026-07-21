#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-020e-app-factory"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

FEATURE_BRANCH="feat/market-data-api"

mkdir -p "$REPORT_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"
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

CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
echo "Current branch: $CURRENT_BRANCH"

if [ "$STATUS" = "success" ] && [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
  echo "Trying to checkout $FEATURE_BRANCH..."

  if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
    if git checkout "$FEATURE_BRANCH"; then
      CURRENT_BRANCH="$FEATURE_BRANCH"
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "true"
      echo "OK: checked out $FEATURE_BRANCH"
    else
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot checkout feature branch" "needs_human"
      add_error "Cannot checkout $FEATURE_BRANCH."
      echo "FAIL: cannot checkout feature branch"
    fi
  else
    add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Feature branch does not exist" "needs_human"
    add_error "Checkout or create $FEATURE_BRANCH first."
    echo "FAIL: feature branch does not exist"
  fi
else
  add_check "git_branch" "$FEATURE_BRANCH" "true"
  echo "OK: already on $FEATURE_BRANCH"
fi

echo "Current git status:"
git status --short || true

echo "Checking that working tree contains only expected backend/scripts changes..."

PORCELAIN="$(git status --porcelain || true)"
UNEXPECTED="$(printf '%s\n' "$PORCELAIN" \
  | grep -v -E '^\?\? scripts/' \
  | grep -v -E '^\?\? backend/' \
  | grep -v -E '^\?\? reports/' \
  | grep -v -E '^ M backend/' \
  | grep -v -E '^M  backend/' \
  | grep -v -E '^A  backend/' \
  | grep -v -E '^ M docker-compose\.yml' \
  | grep -v -E '^M  docker-compose\.yml' \
  || true)"

if [ -n "$UNEXPECTED" ]; then
  add_check "git_status_expected" "working tree" "false" "Unexpected changes present" "needs_human"
  add_error "Unexpected changes present. Review git status before continuing."
  echo "FAIL: unexpected changes present"
  echo "$UNEXPECTED"
else
  add_check "git_status_expected" "working tree" "true"
  echo "OK: working tree contains only expected changes"
fi

echo "Rewriting backend/app/main.py using create_app factory..."

python - <<'PY_WRITE_MAIN'
from pathlib import Path

content = '''from fastapi import FastAPI

from app.api.market_data import router as market_data_router


def create_app() -> FastAPI:
    application = FastAPI(
        title="Trading Terminal API",
        version="0.1.0",
        description="Backend API for AI-assisted trading terminal",
    )

    application.include_router(market_data_router)

    @application.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "backend",
            "version": "0.1.0",
        }

    return application


app = create_app()

_required_route = "/api/instruments"
_current_routes = {getattr(route, "path", "") for route in app.routes}

if _required_route not in _current_routes:
    raise RuntimeError(
        "Market data router was not registered. "
        f"Routes: {sorted(_current_routes)}"
    )
'''

path = Path("backend/app/main.py")
path.parent.mkdir(parents=True, exist_ok=True)

with path.open("w", encoding="utf-8", newline="\n") as f:
    f.write(content)

print("OK: backend/app/main.py rewritten with create_app factory")
PY_WRITE_MAIN

if [ -f "backend/app/main.py" ]; then
  add_check "file_exists" "backend/app/main.py" "true"
  echo "OK: file exists: backend/app/main.py"
else
  add_check "file_exists" "backend/app/main.py" "false"
  echo "FAIL: file missing: backend/app/main.py"
fi

echo "Host main.py content:"
echo "----- BEGIN HOST main.py -----"
cat backend/app/main.py 2>/dev/null || true
echo "----- END HOST main.py -----"

echo "Rewriting backend/Dockerfile with route assertion..."

cat > backend/Dockerfile <<'DOCKERFILE_EOF'
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

RUN find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN find /app -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

RUN python -c "from app.main import app; routes={getattr(route, 'path', '') for route in app.routes}; assert '/api/instruments' in routes, 'Missing /api/instruments: ' + str(sorted(routes))"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE_EOF

add_check "dockerfile_updated" "backend/Dockerfile" "true"
echo "OK: backend/Dockerfile updated"

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
  echo "Rebuilding backend image without cache..."
  echo "This may take a few minutes."

  if docker compose build --no-cache backend; then
    add_check "docker_compose_build_backend_no_cache" "backend" "true"
    echo "OK: backend image rebuilt without cache"
  else
    add_check "docker_compose_build_backend_no_cache" "backend" "false"
    add_error "Docker build failed. See log for Python/RuntimeError details."
    echo "FAIL: backend image rebuild failed"
  fi
fi

ROUTE_DIAG=""
APP_ROUTES=""
HAS_INSTRUMENTS=""

if [ "$STATUS" = "success" ]; then
  echo "Checking FastAPI routes inside container..."

  if ROUTE_DIAG="$(docker compose run --rm -T backend python -c "from app.main import app; routes=sorted({getattr(route, 'path', '') for route in app.routes}); print('APP_ROUTES=' + ','.join(routes)); print('HAS_INSTRUMENTS=' + ('true' if '/api/instruments' in routes else 'false'))" 2>&1)"; then
    echo "----- BEGIN ROUTE_DIAG -----"
    echo "$ROUTE_DIAG"
    echo "----- END ROUTE_DIAG -----"

    APP_ROUTES="$(printf '%s\n' "$ROUTE_DIAG" | tr -d '\r' | grep -E '^APP_ROUTES=' | head -n1 | cut -d'=' -f2- || true)"
    HAS_INSTRUMENTS="$(printf '%s\n' "$ROUTE_DIAG" | tr -d '\r' | grep -E '^HAS_INSTRUMENTS=' | head -n1 | cut -d'=' -f2- || true)"

    if [ "$HAS_INSTRUMENTS" = "true" ]; then
      add_check "app_routes" "/api/instruments" "true"
      echo "OK: app contains /api/instruments"
    else
      add_check "app_routes" "/api/instruments" "false" "app does not contain /api/instruments"
      add_error "app does not contain /api/instruments after create_app fix."
      echo "FAIL: app does not contain /api/instruments"
    fi
  else
    add_check "route_check_execution" "python route check" "false" "Route check execution failed"
    add_error "Route check execution failed."
    echo "FAIL: route check execution failed"
    echo "$ROUTE_DIAG" || true
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

  git add backend/app/main.py backend/Dockerfile scripts/ 2>/dev/null || true

  add_check "git_add" "git add" "true"
  echo "OK: git add completed"
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking staged files for secrets..."

  STAGED_FILES="$(git diff --cached --name-only || true)"
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
fi

if [ "$STATUS" = "success" ]; then
  if git diff --cached --quiet; then
    add_check "git_commit" "commit" "true" "No changes to commit"
    echo "OK: no changes to commit"
  else
    echo "Creating commit..."

    if git commit -m "fix(task-020): use create_app factory and fail-fast route registration"; then
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

sanitize() {
  printf '%s' "$1" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\'
}

CURRENT_BRANCH_SAFE="$(sanitize "$CURRENT_BRANCH")"
COMMIT_SHA_SAFE="$(sanitize "$COMMIT_SHA")"
APP_ROUTES_SAFE="$(sanitize "$APP_ROUTES")"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "feature_branch": "$FEATURE_BRANCH",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "app_routes": "$APP_ROUTES_SAFE",
    "has_instruments": "$HAS_INSTRUMENTS",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/main.py",
    "backend/Dockerfile",
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

Feature branch: **$FEATURE_BRANCH**  
Current branch: **$CURRENT_BRANCH_SAFE**  
App routes: **$APP_ROUTES_SAFE**  
Has /api/instruments: **$HAS_INSTRUMENTS**  
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
