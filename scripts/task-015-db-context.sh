#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-015-db-context"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

MAIN_BRANCH="main"
FEATURE_BRANCH="feat/db-context"

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
  if [ "$CURRENT_BRANCH" = "$MAIN_BRANCH" ]; then
    echo "Pulling main..."

    if git pull --ff-only origin "$MAIN_BRANCH"; then
      add_check "git_pull_main" "$MAIN_BRANCH" "true"
      echo "OK: main synced"
    else
      add_check "git_pull_main" "$MAIN_BRANCH" "false" "git pull failed" "needs_human"
      add_error "git pull --ff-only origin main failed."
      echo "FAIL: git pull failed"
    fi
  else
    add_check "git_pull_main" "$MAIN_BRANCH" "true" "Skipped because not on main"
    echo "OK: not on main, pull skipped"
  fi
fi

if [ "$STATUS" = "success" ]; then
  echo "Preparing feature branch..."

  if [ "$CURRENT_BRANCH" = "$FEATURE_BRANCH" ]; then
    add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Already on feature branch"
    echo "OK: already on $FEATURE_BRANCH"
  else
    if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
      if git checkout "$FEATURE_BRANCH"; then
        CURRENT_BRANCH="$FEATURE_BRANCH"
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Switched to existing feature branch"
        echo "OK: switched to existing $FEATURE_BRANCH"
      else
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot checkout feature branch" "needs_human"
        add_error "Cannot checkout $FEATURE_BRANCH."
        echo "FAIL: cannot checkout feature branch"
      fi
    else
      if git checkout -b "$FEATURE_BRANCH" "$MAIN_BRANCH"; then
        CURRENT_BRANCH="$FEATURE_BRANCH"
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "true" "Created from main"
        echo "OK: created $FEATURE_BRANCH from $MAIN_BRANCH"
      else
        add_check "git_checkout_feature" "$FEATURE_BRANCH" "false" "Cannot create feature branch"
        add_error "Cannot create $FEATURE_BRANCH."
        echo "FAIL: cannot create feature branch"
      fi
    fi
  fi
fi

echo "Creating database context files..."

mkdir -p docs/architecture
mkdir -p docs/domain
mkdir -p backend/app/core
mkdir -p backend/tests

if [ ! -f "backend/app/core/__init__.py" ]; then
  touch backend/app/core/__init__.py
fi

cat > docs/architecture/database.md <<'DB_DOC_EOF'
# Database Architecture

## Overview

The project uses PostgreSQL.

There are two logical database areas:

1. Market Data / Analytics schema: `trading`
2. Terminal / Operations schema: `terminal`

## Schema: trading

This schema already exists and contains market data and analytics:

- instruments
- candles
- candles_30min_raw
- candles_aggregated
- indicators
- signals
- top_stocks_by_volume

The backend should initially use this schema in read-only mode.

## Schema: terminal

This schema will contain terminal-specific operational data:

- users
- accounts
- broker_connections
- orders
- order_executions
- positions
- portfolio_snapshots
- risk_limits
- risk_checks
- trading_controls
- audit_logs
- strategy_signals

## Conventions

1. Prices use numeric types, not float.
2. Volumes use bigint.
3. Timestamps should preferably use timestamp with time zone.
4. FIGI is an important business identifier.
5. Internal foreign keys should use surrogate IDs.
6. Timeframes should be standardized.
7. Date-suffixed tables should be avoided.
8. Reports should use one table with report_date.

## Access Pattern

API backend:
- async access via SQLAlchemy / asyncpg
- read-only access to trading schema
- read/write access to terminal schema

Analytics/ETL workers:
- may use sync psycopg2 + pandas
- may perform bulk inserts
- may calculate indicators and signals
DB_DOC_EOF

cat > docs/domain/data-model.md <<'DATA_MODEL_DOC_EOF'
# Domain Data Model

## Existing Market Data Entities

### Instrument

Source table:

    trading.instruments

Important fields:

- figi
- ticker
- name
- instrument_type
- class_code
- currency
- min_price_increment
- lot_size
- is_tradable
- isin
- exchange
- country_of_risk
- country_of_risk_name

### Candle

Source tables:

    trading.candles
    trading.candles_30min_raw
    trading.candles_aggregated

Recommended canonical fields:

- instrument_id
- figi
- ticker
- timeframe
- timestamp
- open
- high
- low
- close
- volume
- source
- created_at

### Indicator

Source table:

    trading.indicators

Examples:

- sma_5
- sma_20
- ema_12
- rsi_14
- macd
- atr_14
- bb_upper
- bb_middle
- bb_lower

### Signal

Source table:

    trading.signals

Fields:

- ticker
- timeframe
- timestamp
- signal
- confidence
- price
- summary
- buy_signals
- sell_signals
- total_signals

## Future Terminal Entities

### Order

Fields:

- id
- account_id
- instrument_id
- client_order_id
- broker_order_id
- direction
- order_type
- quantity_lots
- price
- status
- source
- approved_by
- created_at
- updated_at

### OrderExecution

Fields:

- id
- order_id
- broker_execution_id
- quantity_lots
- price
- commission
- executed_at

### Position

Fields:

- id
- account_id
- instrument_id
- quantity_lots
- average_price
- current_price
- unrealized_pnl
- updated_at

### RiskLimit

Fields:

- id
- account_id
- max_order_amount
- max_daily_loss
- max_open_orders
- max_orders_per_minute
- market_orders_enabled
- margin_trading_enabled
- trading_enabled

### AuditLog

Fields:

- id
- user_id
- agent_id
- action
- entity_type
- entity_id
- payload
- created_at
DATA_MODEL_DOC_EOF

cat > backend/app/core/config.py <<'CONFIG_PY_EOF'
import os


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value == "":
        return default
    return value


def get_app_database_url() -> str:
    url = _env("APP_DATABASE_URL")
    if url:
        return url

    user = _env("POSTGRES_USER", "app")
    password = _env("POSTGRES_PASSWORD", "app")
    host = _env("POSTGRES_HOST", "postgres")
    port = _env("POSTGRES_PORT", "5432")
    db = _env("POSTGRES_DB", "trading_terminal")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def get_market_database_url() -> str:
    url = _env("MARKET_DATA_DATABASE_URL")
    if url:
        return url

    return get_app_database_url()


def get_settings() -> dict:
    return {
        "environment": _env("ENVIRONMENT", "dev"),
        "app_database_url": get_app_database_url(),
        "market_database_url": get_market_database_url(),
        "market_data_schema": _env("MARKET_DATA_SCHEMA", "trading"),
        "app_schema": _env("APP_SCHEMA", "terminal"),
        "postgres_host": _env("POSTGRES_HOST", "postgres"),
        "postgres_port": _env("POSTGRES_PORT", "5432"),
        "postgres_db": _env("POSTGRES_DB", "trading_terminal"),
    }
CONFIG_PY_EOF

cat > backend/tests/test_config.py <<'TEST_CONFIG_EOF'
from app.core.config import get_settings


def test_settings_defaults() -> None:
    settings = get_settings()

    assert settings["environment"] == "dev"
    assert settings["market_data_schema"] == "trading"
    assert settings["app_schema"] == "terminal"
    assert isinstance(settings["app_database_url"], str)
    assert isinstance(settings["market_database_url"], str)
TEST_CONFIG_EOF

cat > .env.example <<'ENV_EXAMPLE_EOF'
# Ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434

# Application database
# This is the database used by terminal backend for operational data.
APP_DATABASE_URL=postgresql://app:app@postgres:5432/trading_terminal

# Market data database
# This is your existing analytics/market data database.
# Example if PostgreSQL runs on host machine:
# MARKET_DATA_DATABASE_URL=postgresql://USER:PASSWORD@host.docker.internal:5432/trading
MARKET_DATA_DATABASE_URL=

# Schemas
MARKET_DATA_SCHEMA=trading
APP_SCHEMA=terminal

# Optional PostgreSQL variables used when APP_DATABASE_URL is not set
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=app
POSTGRES_PASSWORD=app
POSTGRES_DB=trading_terminal
ENV_EXAMPLE_EOF

echo "Checking files..."

for f in \
  docs/architecture/database.md \
  docs/domain/data-model.md \
  backend/app/core/__init__.py \
  backend/app/core/config.py \
  backend/tests/test_config.py \
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

    if git commit -m "feat(task-015): add database context and data model docs"; then
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

  if UNEXPECTED_AFTER="$(working_tree_acceptable)"; then
    add_check "git_status_acceptable_after" "working tree" "true" "Clean or only untracked scripts"
    echo "OK: working tree is acceptable after commit"
  else
    add_check "git_status_acceptable_after" "working tree" "false" "Unexpected changes after commit" "needs_human"
    add_error "Unexpected changes after commit."
    echo "FAIL: unexpected changes after commit"
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

CURRENT_BRANCH_SAFE="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
CURRENT_BRANCH_SAFE="$(printf '%s' "$CURRENT_BRANCH_SAFE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
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
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "docs/architecture/database.md",
    "docs/domain/data-model.md",
    "backend/app/core/config.py",
    "backend/tests/test_config.py",
    ".env.example",
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
Current branch: **$CURRENT_BRANCH_SAFE**  
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
