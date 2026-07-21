#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-017-db-connection-check"
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

ENV_CREATED="false"

if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    ENV_CREATED="true"
    add_check "env_file" ".env" "true" "Created from .env.example"
    echo "OK: .env created from .env.example"
  else
    cat > .env <<'ENV_EOF'
PSTGRS_PWD=
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_DB=postgres
MARKET_DATA_SCHEMA=trading
ENV_EOF
    ENV_CREATED="true"
    add_check "env_file" ".env" "true" "Created minimal .env"
    echo "OK: minimal .env created"
  fi

  STATUS="needs_human"
  add_check "env_filled" ".env" "false" "Fill .env and rerun" "needs_human"
  add_error "Fill PSTGRS_PWD and PostgreSQL connection variables in .env, then rerun this script."
  echo "FAIL: .env was created but needs to be filled"
else
  add_check "env_file" ".env" "true" "Already exists"
  echo "OK: .env already exists"
fi

echo "Creating DB connection check module..."

mkdir -p backend/app/db

if [ ! -f "backend/app/db/__init__.py" ]; then
  touch backend/app/db/__init__.py
fi

cat > backend/app/db/check_db_connection.py <<'CHECK_DB_EOF'
import os
import re
import sys

from app.db.db_manager import DBManager


def mask_text(value: str) -> str:
    if not value:
        return value

    value = re.sub(
        r"(password\s*=\s*)([^,\s]+)",
        r"\1***",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"(://[^:/@]+:)([^@]+)(@)",
        r"\1***\3",
        value,
    )

    return value


def get_schema() -> str:
    schema = os.getenv("MARKET_DATA_SCHEMA", "trading").strip()

    if not schema:
        schema = "trading"

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
        raise ValueError("Invalid MARKET_DATA_SCHEMA name")

    return schema


def validate_table_name(table_name: str) -> None:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
        raise ValueError(f"Invalid table name: {table_name}")


def get_count(db: DBManager, schema: str, table_name: str) -> int:
    validate_table_name(table_name)

    query = f'select count(*) as cnt from "{schema}"."{table_name}"'
    result = db.select(query)

    return int(result["data"][0][0])


def main() -> None:
    status = "failed"
    error_message = ""
    schema = ""
    ok_value = ""
    instruments_count = ""
    candles_count = ""
    signals_count = ""
    top_stocks_count = ""

    try:
        schema = get_schema()
        db = DBManager()

        try:
            ok_result = db.select("select 1 as ok")
            ok_value = str(ok_result["data"][0][0])

            instruments_count = str(get_count(db, schema, "instruments"))
            candles_count = str(get_count(db, schema, "candles_aggregated"))
            signals_count = str(get_count(db, schema, "signals"))
            top_stocks_count = str(get_count(db, schema, "top_stocks_by_volume"))

            status = "success"
        finally:
            db.close_pool()

    except Exception as exc:
        status = "failed"
        error_message = mask_text(str(exc).replace("\n", " "))

    print("DB_CHECK_STATUS=" + status)
    print("DB_SCHEMA=" + schema)
    print("DB_OK=" + ok_value)
    print("INSTRUMENTS_COUNT=" + instruments_count)
    print("CANDLES_COUNT=" + candles_count)
    print("SIGNALS_COUNT=" + signals_count)
    print("TOP_STOCKS_COUNT=" + top_stocks_count)
    print("ERROR_MESSAGE=" + error_message)

    sys.exit(0)


if __name__ == "__main__":
    main()
CHECK_DB_EOF

if [ -f "backend/app/db/check_db_connection.py" ]; then
  add_check "file_exists" "backend/app/db/check_db_connection.py" "true"
  echo "OK: file exists: backend/app/db/check_db_connection.py"
else
  add_check "file_exists" "backend/app/db/check_db_connection.py" "false"
  echo "FAIL: file missing: backend/app/db/check_db_connection.py"
fi

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

DB_CHECK_STATUS=""
DB_SCHEMA=""
DB_OK=""
INSTRUMENTS_COUNT=""
CANDLES_COUNT=""
SIGNALS_COUNT=""
TOP_STOCKS_COUNT=""
ERROR_MESSAGE=""

if [ "$STATUS" = "success" ]; then
  echo "Running DB connection check..."

  if CHECK_OUTPUT="$(docker compose run --rm -T backend python -m app.db.check_db_connection 2>&1)"; then
    echo "$CHECK_OUTPUT"

    DB_CHECK_STATUS="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^DB_CHECK_STATUS=' | head -n1 | cut -d'=' -f2- || true)"
    DB_SCHEMA="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^DB_SCHEMA=' | head -n1 | cut -d'=' -f2- || true)"
    DB_OK="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^DB_OK=' | head -n1 | cut -d'=' -f2- || true)"
    INSTRUMENTS_COUNT="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^INSTRUMENTS_COUNT=' | head -n1 | cut -d'=' -f2- || true)"
    CANDLES_COUNT="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^CANDLES_COUNT=' | head -n1 | cut -d'=' -f2- || true)"
    SIGNALS_COUNT="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^SIGNALS_COUNT=' | head -n1 | cut -d'=' -f2- || true)"
    TOP_STOCKS_COUNT="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^TOP_STOCKS_COUNT=' | head -n1 | cut -d'=' -f2- || true)"
    ERROR_MESSAGE="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"

    if [ "$DB_CHECK_STATUS" = "success" ]; then
      add_check "db_check_status" "success" "true"
      echo "OK: DB check success"

      add_check "db_select_1" "$DB_OK" "true"
      add_check "instruments_count" "$INSTRUMENTS_COUNT" "true"
      add_check "candles_count" "$CANDLES_COUNT" "true"
      add_check "signals_count" "$SIGNALS_COUNT" "true"
      add_check "top_stocks_count" "$TOP_STOCKS_COUNT" "true"
    else
      add_check "db_check_status" "failed" "false" "$ERROR_MESSAGE" "needs_human"
      add_error "DB connection check failed: $ERROR_MESSAGE"
      echo "FAIL: DB connection check failed"
    fi
  else
    add_check "db_check_execution" "python -m app.db.check_db_connection" "false" "DB check execution failed" "needs_human"
    add_error "DB check execution failed."
    echo "FAIL: DB check execution failed"
    echo "$CHECK_OUTPUT" || true
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

    if git commit -m "feat(task-017): add DB connection check using DBManager"; then
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
DB_SCHEMA_SAFE="$(printf '%s' "$DB_SCHEMA" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
DB_OK_SAFE="$(printf '%s' "$DB_OK" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
INSTRUMENTS_COUNT_SAFE="$(printf '%s' "$INSTRUMENTS_COUNT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
CANDLES_COUNT_SAFE="$(printf '%s' "$CANDLES_COUNT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
SIGNALS_COUNT_SAFE="$(printf '%s' "$SIGNALS_COUNT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
TOP_STOCKS_COUNT_SAFE="$(printf '%s' "$TOP_STOCKS_COUNT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
ERROR_MESSAGE_SAFE="$(printf '%s' "$ERROR_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

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
    "env_created": $ENV_CREATED,
    "db_check_status": "$DB_CHECK_STATUS",
    "db_schema": "$DB_SCHEMA_SAFE",
    "db_ok": "$DB_OK_SAFE",
    "instruments_count": "$INSTRUMENTS_COUNT_SAFE",
    "candles_count": "$CANDLES_COUNT_SAFE",
    "signals_count": "$SIGNALS_COUNT_SAFE",
    "top_stocks_count": "$TOP_STOCKS_COUNT_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/db/check_db_connection.py",
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
ENV created: **$ENV_CREATED**  
DB check status: **$DB_CHECK_STATUS**  
DB schema: **$DB_SCHEMA_SAFE**  
DB OK: **$DB_OK_SAFE**  
Instruments count: **$INSTRUMENTS_COUNT_SAFE**  
Candles count: **$CANDLES_COUNT_SAFE**  
Signals count: **$SIGNALS_COUNT_SAFE**  
Top stocks count: **$TOP_STOCKS_COUNT_SAFE**  
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
