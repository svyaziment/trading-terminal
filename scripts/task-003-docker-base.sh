#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-003-docker-base"
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

echo "Creating Docker files..."

if [ ! -f "Dockerfile.agent" ]; then
  cat > Dockerfile.agent <<'DOCKERFILE_EOF'
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      git \
      curl \
      ca-certificates && \
    rm -rf /var/lib/apt/lists/*

CMD ["bash"]
DOCKERFILE_EOF
  echo "Created Dockerfile.agent"
else
  echo "Dockerfile.agent already exists, skipping."
fi

if [ ! -f ".dockerignore" ]; then
  cat > .dockerignore <<'DOCKERIGNORE_EOF'
.git
.gitignore
.env
.env.*
.venv
venv
__pycache__
*.pyc
node_modules
dist
build
reports
logs
.DS_Store
Thumbs.db
DOCKERIGNORE_EOF
  echo "Created .dockerignore"
else
  echo ".dockerignore already exists, skipping."
fi

if [ ! -f "docker-compose.yml" ]; then
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
COMPOSE_EOF
  echo "Created docker-compose.yml"
else
  echo "docker-compose.yml already exists, skipping."
fi

if [ ! -f ".env.example" ]; then
  cat > .env.example <<'ENV_EXAMPLE_EOF'
# Use this if Ollama runs on the host machine.
# For Docker Desktop on Windows/Mac, host.docker.internal usually works.
OLLAMA_BASE_URL=http://host.docker.internal:11434

# If Ollama runs in another Docker container/service, use:
# OLLAMA_BASE_URL=http://ollama:11434
ENV_EXAMPLE_EOF
  echo "Created .env.example"
else
  echo ".env.example already exists, skipping."
fi

echo "Checking Docker files..."

for f in \
  Dockerfile.agent \
  .dockerignore \
  docker-compose.yml \
  .env.example
do
  if [ -f "$f" ]; then
    add_check "file_exists" "$f" "true"
    echo "OK: file exists: $f"
  else
    add_check "file_exists" "$f" "false"
    echo "FAIL: file missing: $f"
  fi
done

echo "Checking Docker..."

DOCKER_AVAILABLE="false"
DOCKER_DAEMON_OK="false"
COMPOSE_OK="false"

if command -v docker >/dev/null 2>&1; then
  DOCKER_AVAILABLE="true"
  add_check "command_exists" "docker" "true"
  echo "OK: docker command exists"
else
  add_check "command_exists" "docker" "false" "Docker CLI not found" "needs_human"
  add_error "Install Docker Desktop or Docker Engine, then rerun this script."
  echo "FAIL: docker command not found"
fi

if [ "$DOCKER_AVAILABLE" = "true" ]; then
  if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    DOCKER_DAEMON_OK="true"
    add_check "docker_daemon" "docker" "true"
    echo "OK: docker daemon is running"
  else
    add_check "docker_daemon" "docker" "false" "Docker daemon is not running" "needs_human"
    add_error "Start Docker Desktop or Docker daemon, then rerun this script."
    echo "FAIL: docker daemon is not running"
  fi
fi

if [ "$DOCKER_AVAILABLE" = "true" ]; then
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_OK="true"
    add_check "docker_compose" "docker compose" "true"
    echo "OK: docker compose is available"
  else
    add_check "docker_compose" "docker compose" "false" "Docker Compose plugin not found" "needs_human"
    add_error "Install Docker Compose plugin, then rerun this script."
    echo "FAIL: docker compose is not available"
  fi
fi

if [ "$DOCKER_DAEMON_OK" = "true" ] && [ "$COMPOSE_OK" = "true" ]; then
  echo "Validating docker-compose.yml..."

  if docker compose config -q >/dev/null 2>&1; then
    add_check "compose_config" "docker-compose.yml" "true"
    echo "OK: docker-compose.yml is valid"
  else
    add_check "compose_config" "docker-compose.yml" "false" "docker compose config failed"
    add_error "Run manually: docker compose config"
    echo "FAIL: docker-compose.yml validation failed"
  fi
else
  echo "Skipping docker-compose.yml validation because Docker or Docker Compose is unavailable."
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
    "docker_available": $DOCKER_AVAILABLE,
    "docker_daemon_ok": $DOCKER_DAEMON_OK,
    "docker_compose_ok": $COMPOSE_OK
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "Dockerfile.agent",
    ".dockerignore",
    "docker-compose.yml",
    ".env.example"
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

Docker available: **$DOCKER_AVAILABLE**  
Docker daemon OK: **$DOCKER_DAEMON_OK**  
Docker Compose OK: **$COMPOSE_OK**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
