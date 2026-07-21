#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-001-init-repo"
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

echo "Creating project directories..."

mkdir -p docs/product
mkdir -p docs/architecture
mkdir -p docs/security
mkdir -p docs/domain
mkdir -p agents/orchestrator
mkdir -p agents/devops
mkdir -p config
mkdir -p tasks
mkdir -p scripts
mkdir -p reports
mkdir -p logs

echo "Creating base files..."

if [ ! -f "README.md" ]; then
  cat > README.md <<'README_EOF'
# Trading Terminal

AI-assisted trading terminal for MOEX.

Stack:
- Python
- FastAPI
- PostgreSQL
- Redis
- React
- Tinkoff/T-Bank Invest API
README_EOF
fi

if [ ! -f ".gitignore" ]; then
  cat > .gitignore <<'GITIGNORE_EOF'
# Environment
.env
.env.*

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# Node
node_modules/
dist/
build/

# Logs and reports
logs/
reports/

# OS
.DS_Store
Thumbs.db
GITIGNORE_EOF
fi

if [ ! -f "docs/agent-protocol.md" ]; then
  cat > docs/agent-protocol.md <<'PROTOCOL_EOF'
# Agent Protocol

We use step-by-step task execution.

Each task:
1. Has a unique task ID.
2. Has a bash script.
3. Produces artifacts.
4. Produces checks.
5. Produces report.json.
6. Produces report.md.
7. Produces log.txt.

Primary feedback format:
- reports/<task_id>/report.json

Human-readable feedback:
- reports/<task_id>/report.md

Debug feedback:
- reports/<task_id>/log.txt
PROTOCOL_EOF
fi

if [ ! -f "docs/product/mvp.md" ]; then
  cat > docs/product/mvp.md <<'MVP_EOF'
# MVP

Initial goal:
Build an AI-assisted terminal for algorithmic trading on MOEX.

Broker API:
- Tinkoff/T-Bank Invest API

Initial constraints:
1. Use sandbox only.
2. Production trading is disabled.
3. All trading actions require human confirmation.
4. All actions must be logged.
5. Secrets must not be stored in git.
MVP_EOF
fi

if [ ! -f "docs/architecture/overview.md" ]; then
  cat > docs/architecture/overview.md <<'ARCH_EOF'
# Architecture Overview

High-level modules:

1. Frontend
   - React
   - TypeScript

2. Backend API
   - Python
   - FastAPI

3. Broker Gateway
   - Tinkoff/T-Bank Invest API client
   - sandbox first

4. Market Data Collector
   - candles
   - order books
   - instrument status

5. Trading Gateway
   - order preview
   - risk check
   - human confirmation
   - order submission

6. Risk Engine
   - limits
   - kill switch
   - order validation

7. Storage
   - PostgreSQL
   - Redis

8. Agent System
   - Orchestrator
   - DevOps
   - Backend Developer
   - QA
   - Security/Risk reviewer
ARCH_EOF
fi

if [ ! -f "docs/security/secrets.md" ]; then
  cat > docs/security/secrets.md <<'SECURITY_EOF'
# Secrets Policy

Rules:
1. Do not store secrets in git.
2. Use environment variables.
3. Use sandbox tokens for development.
4. Production tokens are prohibited in tests.
5. Reports and logs must not contain tokens.
SECURITY_EOF
fi

if [ ! -f "agents/orchestrator/prompt.md" ]; then
  cat > agents/orchestrator/prompt.md <<'ORCH_PROMPT_EOF'
# Orchestrator Agent

Placeholder.

This agent will decompose user goals into tasks for other agents.
ORCH_PROMPT_EOF
fi

if [ ! -f "agents/devops/prompt.md" ]; then
  cat > agents/devops/prompt.md <<'DEVOPS_PROMPT_EOF'
# DevOps Agent

Placeholder.

This agent will safely execute infrastructure tasks:
- create project structure
- create git branches
- prepare Docker environment
- validate artifacts
DEVOPS_PROMPT_EOF
fi

if [ ! -f "config/agents.yaml" ]; then
  cat > config/agents.yaml <<'AGENTS_YAML_EOF'
version: 1

models:
  orchestrator: qwen2.5-coder:7b

agents:
  - id: orchestrator
    name: Orchestrator
    prompt_file: agents/orchestrator/prompt.md
    model: qwen2.5-coder:7b
    temperature: 0.0
    output_format: json
    permissions:
      write_code: false
      execute_shell: false
      create_pr: false
      use_production: false

  - id: devops
    name: DevOps
    prompt_file: agents/devops/prompt.md
    model: qwen2.5-coder:7b
    temperature: 0.0
    permissions:
      write_files: true
      execute_shell: true
      create_pr: true
      use_production: false
    allowed_commands:
      - git
      - mkdir
      - touch
      - gh
AGENTS_YAML_EOF
fi

if [ ! -f "tasks/README.md" ]; then
  cat > tasks/README.md <<'TASKS_README_EOF'
# Tasks

This directory stores task definitions.

Task execution reports are stored in:

    reports/<task_id>/

Each task should produce:

    report.json
    report.md
    log.txt
TASKS_README_EOF
fi

echo "Checking files and directories..."

CHECKS_JSON=""
CHECKS_MD=""
STATUS="success"

add_check() {
  local name="$1"
  local path="$2"
  local ok="$3"
  local check_status="failed"

  if [ "$ok" = "true" ]; then
    check_status="passed"
  else
    STATUS="failed"
  fi

  local entry
  entry="$(printf '    {\n      "name": "%s",\n      "path": "%s",\n      "status": "%s",\n      "message": ""\n    }' "$name" "$path" "$check_status")"

  if [ -z "$CHECKS_JSON" ]; then
    CHECKS_JSON="$entry"
  else
    CHECKS_JSON="$CHECKS_JSON,
$entry"
  fi

  CHECKS_MD="$CHECKS_MD
- $check_status: $name \`$path\`"
}

check_dir() {
  local path="$1"
  if [ -d "$path" ]; then
    add_check "dir_exists" "$path" "true"
    echo "OK: dir exists: $path"
  else
    add_check "dir_exists" "$path" "false"
    echo "FAIL: dir missing: $path"
  fi
}

check_file() {
  local path="$1"
  if [ -f "$path" ]; then
    add_check "file_exists" "$path" "true"
    echo "OK: file exists: $path"
  else
    add_check "file_exists" "$path" "false"
    echo "FAIL: file missing: $path"
  fi
}

for d in \
  docs \
  docs/product \
  docs/architecture \
  docs/security \
  docs/domain \
  agents \
  agents/orchestrator \
  agents/devops \
  config \
  tasks \
  scripts \
  reports \
  logs
do
  check_dir "$d"
done

for f in \
  README.md \
  .gitignore \
  docs/agent-protocol.md \
  docs/product/mvp.md \
  docs/architecture/overview.md \
  docs/security/secrets.md \
  agents/orchestrator/prompt.md \
  agents/devops/prompt.md \
  config/agents.yaml \
  tasks/README.md
do
  check_file "$f"
done

echo "Preparing Git..."

GIT_BRANCH=""

if command -v git >/dev/null 2>&1; then
  add_check "command_exists" "git" "true"
  echo "OK: git exists"

  if [ ! -d ".git" ]; then
    echo "Initializing git repository..."
    git init
  fi

  if [ -d ".git" ]; then
    add_check "git_repo" ".git" "true"
    echo "OK: git repository exists"
  else
    add_check "git_repo" ".git" "false"
    echo "FAIL: git repository missing"
  fi

  TARGET_BRANCH="chore/agent-bootstrap"

  if [ -d ".git" ]; then
    CURRENT_BRANCH_BEFORE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"

    if [ "$CURRENT_BRANCH_BEFORE" != "$TARGET_BRANCH" ]; then
      echo "Switching to branch: $TARGET_BRANCH"
      git checkout "$TARGET_BRANCH" 2>/dev/null || git checkout -b "$TARGET_BRANCH"
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
  fi
else
  add_check "command_exists" "git" "false"
  echo "FAIL: git command not found"
fi

FINISHED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [ "$STATUS" = "success" ]; then
  ERRORS_JSON="[]"
  ERRORS_MD="No errors."
else
  ERRORS_JSON='[
    "One or more checks failed."
  ]'
  ERRORS_MD="- One or more checks failed."
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
    "git_branch": "$GIT_BRANCH"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "README.md",
    ".gitignore",
    "docs",
    "agents",
    "config",
    "tasks",
    "scripts"
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

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
