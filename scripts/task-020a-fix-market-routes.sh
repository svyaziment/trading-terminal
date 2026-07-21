#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-020a-fix-market-routes"
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
    add_error "Run task-020-market-data-api first or checkout $FEATURE_BRANCH."
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

echo "Rewriting market data API files..."

mkdir -p backend/app/api

touch backend/app/api/__init__.py

cat > backend/app/api/market_data.py <<'MARKET_DATA_EOF'
import datetime
import decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api", tags=["market-data"])


def serialize_value(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)

    if isinstance(value, datetime.datetime):
        return value.isoformat()

    if isinstance(value, datetime.date):
        return value.isoformat()

    return value


def result_to_records(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = result.get("columns", [])
    data = result.get("data", [])

    records = []

    for row in data:
        record = {}

        for column, value in zip(columns, row):
            record[column] = serialize_value(value)

        records.append(record)

    return records


def get_db():
    try:
        from app.db.db_manager import DBManager

        return DBManager()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        )


@router.get("/instruments")
def get_instruments(
    limit: int = Query(100, ge=1, le=1000),
    ticker: Optional[str] = Query(None),
    figi: Optional[str] = Query(None),
    exchange: Optional[str] = Query(None),
    instrument_type: Optional[str] = Query(None),
):
    clauses = []
    params: Dict[str, Any] = {}

    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker

    if figi:
        clauses.append("figi = %(figi)s")
        params["figi"] = figi

    if exchange:
        clauses.append("exchange = %(exchange)s")
        params["exchange"] = exchange

    if instrument_type:
        clauses.append("instrument_type = %(instrument_type)s")
        params["instrument_type"] = instrument_type

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            figi,
            ticker,
            name,
            instrument_type,
            class_code,
            currency,
            lot_size,
            min_price_increment,
            is_tradable,
            exchange,
            country_of_risk,
            created_at,
            updated_at
        FROM trading.instruments
        {where}
        ORDER BY ticker
        LIMIT %(limit)s
    """

    params["limit"] = limit

    try:
        db = get_db()
        result = db.select(query, params)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    items = result_to_records(result)

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/candles")
def get_candles(
    limit: int = Query(100, ge=1, le=5000),
    ticker: Optional[str] = Query(None),
    figi: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
):
    clauses = []
    params: Dict[str, Any] = {}

    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker

    if figi:
        clauses.append("figi = %(figi)s")
        params["figi"] = figi

    if timeframe:
        clauses.append("timeframe = %(timeframe)s")
        params["timeframe"] = timeframe

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            ticker,
            figi,
            timestamp,
            timeframe,
            open,
            high,
            low,
            close,
            volume,
            created_at
        FROM trading.candles_aggregated
        {where}
        ORDER BY timestamp DESC
        LIMIT %(limit)s
    """

    params["limit"] = limit

    try:
        db = get_db()
        result = db.select(query, params)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    items = result_to_records(result)

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/signals")
def get_signals(
    limit: int = Query(100, ge=1, le=1000),
    ticker: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    signal: Optional[str] = Query(None),
):
    clauses = []
    params: Dict[str, Any] = {}

    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker

    if timeframe:
        clauses.append("timeframe = %(timeframe)s")
        params["timeframe"] = timeframe

    if signal:
        clauses.append("signal = %(signal)s")
        params["signal"] = signal

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            id,
            ticker,
            timeframe,
            timestamp,
            signal,
            confidence,
            price,
            rsi,
            macd,
            bb_position,
            volume_ratio,
            atr_pct,
            summary,
            buy_signals,
            sell_signals,
            total_signals,
            created_at
        FROM trading.signals
        {where}
        ORDER BY timestamp DESC
        LIMIT %(limit)s
    """

    params["limit"] = limit

    try:
        db = get_db()
        result = db.select(query, params)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    items = result_to_records(result)

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/top-stocks-by-volume")
def get_top_stocks_by_volume(
    limit: int = Query(100, ge=1, le=1000),
    report_date: Optional[str] = Query(None),
    ticker: Optional[str] = Query(None),
):
    clauses = []
    params: Dict[str, Any] = {}

    if report_date:
        clauses.append("report_date = %(report_date)s::date")
        params["report_date"] = report_date

    if ticker:
        clauses.append("ticker = %(ticker)s")
        params["ticker"] = ticker

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            rank,
            report_date,
            ticker,
            figi,
            name,
            sum_volume,
            candle_count,
            first_date,
            last_date,
            period_start,
            period_end,
            created_at
        FROM trading.top_stocks_by_volume
        {where}
        ORDER BY report_date DESC, rank ASC
        LIMIT %(limit)s
    """

    params["limit"] = limit

    try:
        db = get_db()
        result = db.select(query, params)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    items = result_to_records(result)

    return {
        "items": items,
        "count": len(items),
    }
MARKET_DATA_EOF

cat > backend/app/main.py <<'MAIN_EOF'
from fastapi import FastAPI

from app.api.market_data import router as market_data_router


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


app.include_router(market_data_router)
MAIN_EOF

cat > backend/tests/test_routes.py <<'TEST_ROUTES_EOF'
from app.main import app


def test_api_routes_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/health" in paths
    assert "/api/instruments" in paths
    assert "/api/candles" in paths
    assert "/api/signals" in paths
    assert "/api/top-stocks-by-volume" in paths
TEST_ROUTES_EOF

echo "Checking files..."

for f in \
  backend/app/api/__init__.py \
  backend/app/api/market_data.py \
  backend/app/main.py \
  backend/tests/test_routes.py
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
  echo "Rebuilding backend image without cache..."
  echo "This may take a few minutes."

  if docker compose build --no-cache backend; then
    add_check "docker_compose_build_backend_no_cache" "backend" "true"
    echo "OK: backend image rebuilt without cache"
  else
    add_check "docker_compose_build_backend_no_cache" "backend" "false"
    add_error "Run manually: docker compose build --no-cache backend"
    echo "FAIL: backend image rebuild failed"
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

ROUTES_OUTPUT=""

if [ "$STATUS" = "success" ]; then
  echo "Checking FastAPI routes..."

  if ROUTES_OUTPUT="$(docker compose run --rm -T backend python -c "from app.main import app; print('APP_ROUTES=' + ','.join(sorted({getattr(route, 'path', '') for route in app.routes})))" 2>&1)"; then
    echo "$ROUTES_OUTPUT"

    if printf '%s' "$ROUTES_OUTPUT" | grep -q "/api/instruments"; then
      add_check "route_instruments" "/api/instruments" "true"
      echo "OK: /api/instruments route exists"
    else
      add_check "route_instruments" "/api/instruments" "false"
      add_error "/api/instruments route not found."
      echo "FAIL: /api/instruments route not found"
    fi

    if printf '%s' "$ROUTES_OUTPUT" | grep -q "/api/candles"; then
      add_check "route_candles" "/api/candles" "true"
      echo "OK: /api/candles route exists"
    else
      add_check "route_candles" "/api/candles" "false"
      add_error "/api/candles route not found."
      echo "FAIL: /api/candles route not found"
    fi

    if printf '%s' "$ROUTES_OUTPUT" | grep -q "/api/signals"; then
      add_check "route_signals" "/api/signals" "true"
      echo "OK: /api/signals route exists"
    else
      add_check "route_signals" "/api/signals" "false"
      add_error "/api/signals route not found."
      echo "FAIL: /api/signals route not found"
    fi

    if printf '%s' "$ROUTES_OUTPUT" | grep -q "/api/top-stocks-by-volume"; then
      add_check "route_top_stocks" "/api/top-stocks-by-volume" "true"
      echo "OK: /api/top-stocks-by-volume route exists"
    else
      add_check "route_top_stocks" "/api/top-stocks-by-volume" "false"
      add_error "/api/top-stocks-by-volume route not found."
      echo "FAIL: /api/top-stocks-by-volume route not found"
    fi
  else
    add_check "routes_check" "app.main" "false" "Cannot import app.main"
    add_error "Cannot import app.main inside backend container."
    echo "FAIL: cannot import app.main"
    echo "$ROUTES_OUTPUT" || true
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

    if git commit -m "fix(task-020): register market data API routes"; then
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
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/api/__init__.py",
    "backend/app/api/market_data.py",
    "backend/app/main.py",
    "backend/tests/test_routes.py",
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
