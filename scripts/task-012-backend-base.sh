#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-012-backend-base"
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

if [ -n "$(git status --porcelain)" ]; then
  add_check "git_status_clean" "working tree" "false" "Working tree is not clean" "needs_human"
  add_error "Working tree is not clean. Commit or stash changes before continuing."
  echo "FAIL: working tree is not clean"
  git status --short || true
else
  add_check "git_status_clean" "working tree" "true"
  echo "OK: working tree is clean"
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

if [ "$STATUS" = "success" ]; then
  echo "Checking out main..."

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

if [ "$STATUS" = "success" ]; then
  echo "Pulling main..."

  if git pull --ff-only origin "$MAIN_BRANCH"; then
    add_check "git_pull_main" "$MAIN_BRANCH" "true"
    echo "OK: main synced"
  else
    add_check "git_pull_main" "$MAIN_BRANCH" "false" "git pull failed" "needs_human"
    add_error "git pull --ff-only origin main failed."
    echo "FAIL: git pull failed"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Checking out feature branch..."

  if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
    if git checkout "$FEATURE_BRANCH"; then
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Branch already exists"
      echo "OK: checked out existing $FEATURE_BRANCH"
    else
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot checkout feature branch"
      add_error "Cannot checkout feature branch."
      echo "FAIL: cannot checkout feature branch"
    fi
  else
    if git checkout -b "$FEATURE_BRANCH" "$MAIN_BRANCH"; then
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Created from main"
      echo "OK: created $FEATURE_BRANCH from $MAIN_BRANCH"
    else
      add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot create feature branch"
      add_error "Cannot create feature branch."
      echo "FAIL: cannot create feature branch"
    fi
  fi
fi

echo "Creating backend files..."

mkdir -p backend/app
mkdir -p backend/tests

if [ ! -f "backend/app/__init__.py" ]; then
  touch backend/app/__init__.py
fi

if [ ! -f "backend/tests/__init__.py" ]; then
  touch backend/tests/__init__.py
fi

if [ ! -f "backend/app/main.py" ]; then
  cat > backend/app/main.py <<'MAIN_PY_EOF'
from fastapi import FastAPI

app = FastAPI(
    title="Trading Terminal API",
    version="0.1.0",
    description="Backend API for AI-assisted trading terminal",
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "backend",
        "version": "0.1.0",
    }
MAIN_PY_EOF
fi

if [ ! -f "backend/tests/test_health.py" ]; then
  cat > backend/tests/test_health.py <<'TEST_PY_EOF'
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "backend"
TEST_PY_EOF
fi

if [ ! -f "backend/requirements.txt" ]; then
  cat > backend/requirements.txt <<'REQ_EOF'
fastapi
uvicorn
REQ_EOF
fi

if [ ! -f "backend/requirements-dev.txt" ]; then
  cat > backend/requirements-dev.txt <<'REQ_DEV_EOF'
-r requirements.txt
pytest
httpx
REQ_DEV_EOF
fi

if [ ! -f "backend/.dockerignore" ]; then
  cat > backend/.dockerignore <<'DOCKERIGNORE_EOF'
__pycache__
*.pyc
*.pyo
.env
.env.*
.pytest_cache
.git
.gitignore
DOCKERIGNORE_EOF
fi

if [ ! -f "backend/Dockerfile" ]; then
  cat > backend/Dockerfile <<'DOCKERFILE_EOF'
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE_EOF
fi

if [ ! -f "backend/pyproject.toml" ]; then
  cat > backend/pyproject.toml <<'PYPROJECT_EOF'
[project]
name = "trading-terminal-backend"
version = "0.1.0"
description = "Backend API for AI-assisted trading terminal"
requires-python = ">=3.12"

dependencies = [
    "fastapi",
    "uvicorn",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "httpx",
]
PYPROJECT_EOF
fi

echo "Updating docker-compose.yml..."

cat > docker-compose.yml <<'COMPOSE_EOF'
name: trading-terminal

services:
  agent:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: trading-terminal-agent
    working_dir: /app
    volumes:
      - .:/app
    environment:
      OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
    command: bash
    stdin_open: true
    tty: true

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: trading-terminal-backend
    ports:
      - "8000:8000"
    environment:
      ENVIRONMENT: dev
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5
COMPOSE_EOF

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

CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

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
    "current_branch": "$CURRENT_BRANCH_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/main.py",
    "backend/tests/test_health.py",
    "backend/Dockerfile",
    "docker-compose.yml"
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
Current branch: **$CURRENT_BRANCH_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
