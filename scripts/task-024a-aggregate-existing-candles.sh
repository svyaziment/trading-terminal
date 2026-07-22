#!/usr/bin/env bash

set -u

TASK_ID="task-024a-aggregate-existing-candles"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

mkdir -p "$REPORT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STATUS="success"
ERROR_MESSAGE=""
CHECKS_JSON=""
COMMIT_CREATED=false
COMMIT_SHA=""
RAW_COUNT=""
AGG_30MIN=""
AGG_1H=""
AGG_4H=""
AGG_1D=""
AGG_1W=""
AGG_1M=""

sanitize() {
  printf '%s' "$1" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\'
}

add_check() {
  local name="$1"
  local path="$2"
  local ok="$3"
  local message="${4:-}"

  local check_status="failed"
  if [ "$ok" = "true" ]; then
    check_status="passed"
  else
    STATUS="failed"
  fi

  local entry
  entry="$(printf '    {"name": "%s", "path": "%s", "status": "%s", "message": "%s"}' "$name" "$path" "$check_status" "$message")"

  if [ -z "$CHECKS_JSON" ]; then
    CHECKS_JSON="$entry"
  else
    CHECKS_JSON="$CHECKS_JSON,
$entry"
  fi
}

write_report() {
  local finished_at
  finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  local errors_json="[]"
  if [ -n "$ERROR_MESSAGE" ]; then
    errors_json="[\"$(sanitize "$ERROR_MESSAGE")\"]"
  fi

  cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$finished_at",
  "environment": {
    "cwd": "$ROOT_DIR",
    "branch": "$(sanitize "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')")",
    "raw_count": "$(sanitize "$RAW_COUNT")",
    "agg_30min": "$(sanitize "$AGG_30MIN")",
    "agg_1h": "$(sanitize "$AGG_1H")",
    "agg_4h": "$(sanitize "$AGG_4H")",
    "agg_1d": "$(sanitize "$AGG_1D")",
    "agg_1w": "$(sanitize "$AGG_1W")",
    "agg_1m": "$(sanitize "$AGG_1M")",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$(sanitize "$COMMIT_SHA")"
  },
  "checks": [
$CHECKS_JSON
  ],
  "errors": $errors_json,
  "log_file": "reports/$TASK_ID/log.txt"
}
EOF

  cat > "$REPORT_MD" <<EOF
# $TASK_ID

Status: **$STATUS**

Started: $STARTED_AT  
Finished: $finished_at

Branch: **$(sanitize "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')")**  
Raw candles: **$(sanitize "$RAW_COUNT")**  
Agg 30min: **$(sanitize "$AGG_30MIN")**  
Agg 1h: **$(sanitize "$AGG_1H")**  
Agg 4h: **$(sanitize "$AGG_4H")**  
Agg 1d: **$(sanitize "$AGG_1D")**  
Agg 1w: **$(sanitize "$AGG_1W")**  
Agg 1M: **$(sanitize "$AGG_1M")**  
Commit created: **$COMMIT_CREATED**  
Commit SHA: **$(sanitize "$COMMIT_SHA")**

## Error

$ERROR_MESSAGE
EOF
}

fail() {
  STATUS="failed"
  ERROR_MESSAGE="$1"
  add_check "fatal" "script" "false" "$1"
  write_report
  exit 1
}

echo "=== Task: $TASK_ID ==="
echo "Started: $STARTED_AT"
echo "Working directory: $ROOT_DIR"

cd "$ROOT_DIR" || fail "cannot cd to $ROOT_DIR"

echo "Checking git..."
command -v git >/dev/null 2>&1 || fail "git not found"
add_check "git_exists" "git" "true"

[ -d .git ] || fail "not a git repository"
add_check "git_repo" ".git" "true"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
echo "Current branch: $CURRENT_BRANCH"

echo "Checking Docker..."
if docker info >/dev/null 2>&1; then
  add_check "docker_daemon" "docker" "true"
  echo "OK: docker daemon is running"
else
  fail "Docker daemon is not running. Start Docker Desktop first."
fi

echo "Creating analytics module..."

mkdir -p backend/app/analytics

cat > backend/app/analytics/__init__.py <<'PY'
PY

cat > backend/app/analytics/aggregate_candles.py <<'PY'
from app.db.db_manager import DBManager


TIMEFRAMES = ["30min", "1h", "4h", "1d", "1w", "1M"]

BUCKET_EXPR = {
    "30min": "timestamp",
    "1h": "date_trunc('hour', timestamp)",
    "4h": "date_trunc('hour', timestamp) - (EXTRACT(hour FROM timestamp)::int % 4) * interval '1 hour'",
    "1d": "date_trunc('day', timestamp)",
    "1w": "date_trunc('week', timestamp)",
    "1M": "date_trunc('month', timestamp)",
}


def aggregate_timeframe(db: DBManager, timeframe: str) -> int:
    bucket_expr = BUCKET_EXPR[timeframe]

    delete_sql = f"""
        DELETE FROM trading.candles_aggregated
        WHERE timeframe = %s
          AND timestamp IN (
              SELECT DISTINCT {bucket_expr}
              FROM trading.candles_30min_raw
              WHERE timestamp IS NOT NULL
          )
    """

    db.execute(delete_sql, (timeframe,))

    insert_sql = f"""
        INSERT INTO trading.candles_aggregated (
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
        )
        SELECT
            ticker,
            figi,
            {bucket_expr} AS timestamp,
            %s,
            (array_agg(open ORDER BY timestamp ASC))[1],
            max(high),
            min(low),
            (array_agg(close ORDER BY timestamp DESC))[1],
            coalesce(sum(volume), 0),
            now()
        FROM trading.candles_30min_raw
        WHERE timestamp IS NOT NULL
        GROUP BY
            ticker,
            figi,
            {bucket_expr}
    """

    return db.execute(insert_sql, (timeframe,))


def aggregate_all() -> None:
    db = DBManager()

    raw = db.select("SELECT count(*) AS cnt FROM trading.candles_30min_raw")
    raw_df = raw.to_dataframe()
    raw_count = int(raw_df.iloc[0]["cnt"]) if not raw_df.empty else 0
    print(f"RAW_COUNT={raw_count}")

    for timeframe in TIMEFRAMES:
        count = aggregate_timeframe(db, timeframe)
        key = timeframe.upper().replace("MIN", "MIN")
        print(f"AGG_{key}={count}")

    db.close_pool()


if __name__ == "__main__":
    aggregate_all()
PY

add_check "analytics_module" "backend/app/analytics/aggregate_candles.py" "true"
echo "OK: analytics module created"

echo "Building backend image..."
if docker compose build backend; then
  add_check "docker_build" "backend" "true"
  echo "OK: backend built"
else
  fail "docker compose build backend failed"
fi

echo "Running aggregation from existing candles_30min_raw..."

if RUN_OUTPUT="$(docker compose run --rm -T backend python -m app.analytics.aggregate_candles 2>&1)"; then
  echo "$RUN_OUTPUT"

  RAW_COUNT="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^RAW_COUNT=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_30MIN="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_30MIN=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_1H="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_1H=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_4H="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_4H=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_1D="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_1D=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_1W="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_1W=' | head -n1 | cut -d'=' -f2- || true)"
  AGG_1M="$(printf '%s\n' "$RUN_OUTPUT" | tr -d '\r' | grep -E '^AGG_1M=' | head -n1 | cut -d'=' -f2- || true)"

  add_check "aggregate_run" "app.analytics.aggregate_candles" "true"
  echo "OK: aggregation completed"
else
  echo "$RUN_OUTPUT" || true
  fail "aggregation run failed"
fi

if [ -z "$RAW_COUNT" ]; then
  fail "RAW_COUNT not found in aggregation output"
fi

add_check "raw_count" "trading.candles_30min_raw" "true" "count=$RAW_COUNT"

echo "Staging files..."
git add backend/app/analytics scripts 2>/dev/null || true
add_check "git_add" "git add" "true"

echo "Checking staged files for secrets..."
STAGED_FILES="$(git diff --cached --name-only || true)"
if printf '%s\n' "$STAGED_FILES" | grep -E '(^|/)\.env$|\.env\.|settings\.yaml$|\.pem$|\.key$|id_rsa' >/dev/null 2>&1; then
  git reset -- >/dev/null 2>&1 || true
  fail "secret-like file staged; staged files were reset"
fi

add_check "secret_check" "staged files" "true"

if git diff --cached --quiet; then
  echo "OK: no changes to commit"
else
  if git commit -m "feat(task-024): add non-interactive candle aggregation"; then
    COMMIT_CREATED=true
    add_check "git_commit" "commit" "true"
    echo "OK: commit created"
  else
    fail "git commit failed"
  fi
fi

COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '')"

write_report

echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
