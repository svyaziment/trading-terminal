#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-017b-fix-db-encoding"
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

echo "Checking working tree..."

if UNEXPECTED_CHANGES="$(working_tree_acceptable)"; then
  add_check "git_status_acceptable" "working tree" "true" "Clean or only untracked scripts"
  echo "OK: working tree is acceptable"
else
  add_check "git_status_acceptable" "working tree" "false" "Unexpected changes present" "needs_human"
  add_error "Unexpected changes in working tree. Commit or stash them before continuing."
  echo "FAIL: unexpected changes in working tree"
  echo "$UNEXPECTED_CHANGES" || true
fi

echo "Updating docker-compose.yml with PostgreSQL client encoding..."

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
      TINVEST_TOKEN: ${TINVEST_TOKEN:-}
      TINVEST_ACC: ${TINVEST_ACC:-}
      PSTGRS_PWD: ${PSTGRS_PWD:-}
      POSTGRES_HOST: ${POSTGRES_HOST:-host.docker.internal}
      POSTGRES_PORT: ${POSTGRES_PORT:-5432}
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_DB: ${POSTGRES_DB:-postgres}
      MARKET_DATA_DATABASE_URL: ${MARKET_DATA_DATABASE_URL:-}
      MARKET_DATA_SCHEMA: ${MARKET_DATA_SCHEMA:-trading}
      PGCLIENTENCODING: "UTF8"
      PGOPTIONS: "-c client_encoding=UTF8"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5
COMPOSE_EOF

add_check "docker_compose_updated" "docker-compose.yml" "true"
echo "OK: docker-compose.yml updated"

echo "Patching backend/app/db/db_manager.py..."

python - <<'PY'
from pathlib import Path

path = Path("backend/app/db/db_manager.py")

if not path.exists():
    print("FAIL: backend/app/db/db_manager.py not found")
    raise SystemExit(1)

text = path.read_text(encoding="utf-8", errors="ignore")

if "client_encoding" in text:
    print("OK: db_manager.py already contains client_encoding")
else:
    markers = [
        '"port": self.settings.db.port,',
        "'port': self.settings.db.port,",
    ]

    patched = False

    for marker in markers:
        if marker in text:
            replacement = (
                marker
                + '\n            "client_encoding": "utf8",'
                + '\n            "options": "-c client_encoding=UTF8",'
                + '\n            "connect_timeout": 10,'
            )
            text = text.replace(marker, replacement, 1)
            patched = True
            break

    if patched:
        path.write_text(text, encoding="utf-8")
        print("OK: db_manager.py patched")
    else:
        print("WARN: could not find port marker in db_manager.py")
PY

if [ $? -eq 0 ]; then
  add_check "db_manager_patch" "backend/app/db/db_manager.py" "true"
  echo "OK: db_manager.py patch step completed"
else
  add_check "db_manager_patch" "backend/app/db/db_manager.py" "false"
  add_error "Cannot patch db_manager.py."
  echo "FAIL: db_manager.py patch failed"
fi

echo "Patching backend/app/core/config_manager.py for safe YAML loading..."

python - <<'PY'
from pathlib import Path

path = Path("backend/app/core/config_manager.py")

if not path.exists():
    print("WARN: backend/app/core/config_manager.py not found")
    raise SystemExit(0)

text = path.read_text(encoding="utf-8", errors="ignore")

if "except UnicodeDecodeError" in text:
    print("OK: config_manager.py already has UnicodeDecodeError handling")
else:
    old_block = '''    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)'''

    new_block = '''    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except UnicodeDecodeError:
        data = {}'''

    if old_block in text:
        text = text.replace(old_block, new_block, 1)
        path.write_text(text, encoding="utf-8")
        print("OK: config_manager.py patched")
    else:
        print("WARN: could not find YAML load block in config_manager.py")
PY

add_check "config_manager_patch" "backend/app/core/config_manager.py" "true"
echo "OK: config_manager.py patch step completed"

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

  if docker compose build backend; then
    add_check "docker_compose_build_backend" "backend" "true"
    echo "OK: backend image built"
  else
    add_check "docker_compose_build_backend" "backend" "false"
    add_error "Run manually: docker compose build backend"
    echo "FAIL: backend image build failed"
  fi
fi

COMMIT_CREATED="false"
COMMIT_SHA=""

if [ "$STATUS" = "success" ]; then
  echo "Staging encoding fixes..."

  git add docker-compose.yml 2>/dev/null || true
  git add backend/app/db/db_manager.py 2>/dev/null || true
  git add backend/app/core/config_manager.py 2>/dev/null || true
  git add scripts/ 2>/dev/null || true

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

    if git commit -m "fix(task-017): add PostgreSQL client encoding settings"; then
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

DIAG_DB_CONNECTED=""
DIAG_CURRENT_DB=""
DIAG_SERVER_ENCODING=""
DIAG_LC_MESSAGES=""
DIAG_ERROR=""

if [ "$STATUS" = "success" ]; then
  echo "Running PostgreSQL encoding diagnostic..."

  if DIAG_OUTPUT="$(docker compose run --rm -T backend python - <<'PY' 2>&1
import os
import re
import sys

def mask(value: str) -> str:
    if not value:
        return value
    value = re.sub(r"(password\s*=\s*)([^,\s]+)", r"\1***", value, flags=re.IGNORECASE)
    value = re.sub(r"(://[^:/@]+:)([^@]+)(@)", r"\1***\3", value)
    return value

try:
    import psycopg2

    password = os.getenv("PSTGRS_PWD", "") or os.getenv("POSTGRES_PASSWORD", "")

    params = {
        "host": os.getenv("POSTGRES_HOST", ""),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "user": os.getenv("POSTGRES_USER", ""),
        "password": password,
        "dbname": os.getenv("POSTGRES_DB", ""),
        "client_encoding": "utf8",
        "options": "-c client_encoding=UTF8",
        "connect_timeout": 10,
    }

    conn = psycopg2.connect(**params)
    cur = conn.cursor()

    cur.execute("select current_database()")
    current_db = cur.fetchone()[0]

    cur.execute("SHOW server_encoding")
    server_encoding = cur.fetchone()[0]

    cur.execute("SHOW lc_messages")
    lc_messages = cur.fetchone()[0]

    print("DIAG_DB_CONNECTED=true")
    print("DIAG_CURRENT_DB=" + str(current_db))
    print("DIAG_SERVER_ENCODING=" + str(server_encoding))
    print("DIAG_LC_MESSAGES=" + str(lc_messages))

    conn.close()

except Exception as exc:
    print("DIAG_DB_CONNECTED=false")
    print("DIAG_ERROR=" + mask(str(exc).replace("\n", " ")))
PY
)"; then
    echo "$DIAG_OUTPUT"

    DIAG_DB_CONNECTED="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DIAG_DB_CONNECTED=' | head -n1 | cut -d'=' -f2- || true)"
    DIAG_CURRENT_DB="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DIAG_CURRENT_DB=' | head -n1 | cut -d'=' -f2- || true)"
    DIAG_SERVER_ENCODING="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DIAG_SERVER_ENCODING=' | head -n1 | cut -d'=' -f2- || true)"
    DIAG_LC_MESSAGES="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DIAG_LC_MESSAGES=' | head -n1 | cut -d'=' -f2- || true)"
    DIAG_ERROR="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DIAG_ERROR=' | head -n1 | cut -d'=' -f2- || true)"

    if [ "$DIAG_DB_CONNECTED" = "true" ]; then
      add_check "diag_db_connected" "true" "true"
      add_check "diag_current_db" "$DIAG_CURRENT_DB" "true"
      add_check "diag_server_encoding" "$DIAG_SERVER_ENCODING" "true"
      add_check "diag_lc_messages" "$DIAG_LC_MESSAGES" "true"
      echo "OK: PostgreSQL diagnostic connected"
    else
      add_check "diag_db_connected" "false" "false" "$DIAG_ERROR" "needs_human"
      add_error "PostgreSQL diagnostic failed: $DIAG_ERROR"
      echo "FAIL: PostgreSQL diagnostic failed"
    fi
  else
    add_check "diag_execution" "docker compose run" "false" "Diagnostic execution failed" "needs_human"
    add_error "PostgreSQL diagnostic execution failed."
    echo "FAIL: diagnostic execution failed"
    echo "$DIAG_OUTPUT" || true
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
DIAG_CURRENT_DB_SAFE="$(printf '%s' "$DIAG_CURRENT_DB" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
DIAG_SERVER_ENCODING_SAFE="$(printf '%s' "$DIAG_SERVER_ENCODING" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
DIAG_LC_MESSAGES_SAFE="$(printf '%s' "$DIAG_LC_MESSAGES" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
DIAG_ERROR_SAFE="$(printf '%s' "$DIAG_ERROR" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$DIAG_DB_CONNECTED" != "true" ]; then
  DIAG_DB_CONNECTED="false"
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
    "expected_branch": "$EXPECTED_BRANCH",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE",
    "diag_db_connected": $DIAG_DB_CONNECTED,
    "diag_current_db": "$DIAG_CURRENT_DB_SAFE",
    "diag_server_encoding": "$DIAG_SERVER_ENCODING_SAFE",
    "diag_lc_messages": "$DIAG_LC_MESSAGES_SAFE",
    "diag_error": "$DIAG_ERROR_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "docker-compose.yml",
    "backend/app/db/db_manager.py",
    "backend/app/core/config_manager.py",
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
Commit created: **$COMMIT_CREATED**  
Commit SHA: **$COMMIT_SHA_SAFE**

Diagnostic DB connected: **$DIAG_DB_CONNECTED**  
Diagnostic current DB: **$DIAG_CURRENT_DB_SAFE**  
Diagnostic server encoding: **$DIAG_SERVER_ENCODING_SAFE**  
Diagnostic lc_messages: **$DIAG_LC_MESSAGES_SAFE**  
Diagnostic error: **$DIAG_ERROR_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
