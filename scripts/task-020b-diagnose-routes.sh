#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-020b-diagnose-routes"
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

echo "Checking host files..."

HOST_MAIN_BYTES="$(wc -c < backend/app/main.py 2>/dev/null | tr -d ' \r\n' || echo "-1")"
HOST_MARKET_BYTES="$(wc -c < backend/app/api/market_data.py 2>/dev/null | tr -d ' \r\n' || echo "-1")"
HOST_TEST_ROUTES_BYTES="$(wc -c < backend/tests/test_routes.py 2>/dev/null | tr -d ' \r\n' || echo "-1")"

echo "HOST_MAIN_BYTES=$HOST_MAIN_BYTES"
echo "HOST_MARKET_BYTES=$HOST_MARKET_BYTES"
echo "HOST_TEST_ROUTES_BYTES=$HOST_TEST_ROUTES_BYTES"

if [ "$HOST_MAIN_BYTES" != "-1" ] && [ "$HOST_MAIN_BYTES" != "0" ]; then
  add_check "host_main_py" "backend/app/main.py" "true" "size=$HOST_MAIN_BYTES"
else
  add_check "host_main_py" "backend/app/main.py" "false" "missing or empty"
  add_error "backend/app/main.py is missing or empty on host."
fi

if [ "$HOST_MARKET_BYTES" != "-1" ] && [ "$HOST_MARKET_BYTES" != "0" ]; then
  add_check "host_market_data_py" "backend/app/api/market_data.py" "true" "size=$HOST_MARKET_BYTES"
else
  add_check "host_market_data_py" "backend/app/api/market_data.py" "false" "missing or empty"
  add_error "backend/app/api/market_data.py is missing or empty on host."
fi

echo "Host backend/app/main.py:"
echo "----- BEGIN HOST main.py -----"
cat backend/app/main.py 2>/dev/null || true
echo "----- END HOST main.py -----"

echo "Host backend/app/api/market_data.py first 80 lines:"
echo "----- BEGIN HOST market_data.py -----"
head -n 80 backend/app/api/market_data.py 2>/dev/null || true
echo "----- END HOST market_data.py -----"

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
  echo "Checking container files..."

  docker compose run --rm -T backend sh -lc '
    echo "----- BEGIN CONTAINER ls /app/app -----";
    ls -la /app/app || true;
    echo "----- END CONTAINER ls /app/app -----";

    echo "----- BEGIN CONTAINER ls /app/app/api -----";
    ls -la /app/app/api || true;
    echo "----- END CONTAINER ls /app/app/api -----";

    echo "----- BEGIN CONTAINER main.py -----";
    cat /app/app/main.py 2>/dev/null || true;
    echo "----- END CONTAINER main.py -----";

    echo "----- BEGIN CONTAINER market_data.py first 80 lines -----";
    head -n 80 /app/app/api/market_data.py 2>/dev/null || true;
    echo "----- END CONTAINER market_data.py first 80 lines -----";
  ' || true
fi

PYTHON_DIAG=""

if [ "$STATUS" = "success" ]; then
  echo "Running Python route diagnostic inside backend container..."

  PYTHON_DIAG="$(docker compose run --rm -T backend python - <<'PY'
import os

main_path = "/app/app/main.py"
market_path = "/app/app/api/market_data.py"

print("CONTAINER_MAIN_BYTES=" + str(os.path.getsize(main_path) if os.path.exists(main_path) else -1))
print("CONTAINER_MARKET_BYTES=" + str(os.path.getsize(market_path) if os.path.exists(market_path) else -1))

try:
    import app.main as main_module

    print("MAIN_FILE=" + str(getattr(main_module, "__file__", "")))
    print("APP_ROUTES=" + ",".join(sorted({getattr(route, "path", "") for route in main_module.app.routes})))
except Exception as exc:
    print("MAIN_IMPORT_ERROR=" + repr(exc))

try:
    import app.api.market_data as market_module

    print("MARKET_FILE=" + str(getattr(market_module, "__file__", "")))
    print("MARKET_ROUTES=" + ",".join(sorted({getattr(route, "path", "") for route in market_module.router.routes})))
except Exception as exc:
    print("MARKET_IMPORT_ERROR=" + repr(exc))
PY
)"

  echo "----- BEGIN PYTHON_DIAG -----"
  echo "$PYTHON_DIAG"
  echo "----- END PYTHON_DIAG -----"
fi

CONTAINER_MAIN_BYTES="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^CONTAINER_MAIN_BYTES=' | head -n1 | cut -d'=' -f2- || echo "-1")"
CONTAINER_MARKET_BYTES="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^CONTAINER_MARKET_BYTES=' | head -n1 | cut -d'=' -f2- || echo "-1")"
MAIN_FILE="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^MAIN_FILE=' | head -n1 | cut -d'=' -f2- || echo "")"
APP_ROUTES="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^APP_ROUTES=' | head -n1 | cut -d'=' -f2- || echo "")"
MARKET_FILE="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^MARKET_FILE=' | head -n1 | cut -d'=' -f2- || echo "")"
MARKET_ROUTES="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^MARKET_ROUTES=' | head -n1 | cut -d'=' -f2- || echo "")"
MAIN_IMPORT_ERROR="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^MAIN_IMPORT_ERROR=' | head -n1 | cut -d'=' -f2- || echo "")"
MARKET_IMPORT_ERROR="$(printf '%s\n' "$PYTHON_DIAG" | tr -d '\r' | grep -E '^MARKET_IMPORT_ERROR=' | head -n1 | cut -d'=' -f2- || echo "")"

echo "Parsed diagnostic:"
echo "CONTAINER_MAIN_BYTES=$CONTAINER_MAIN_BYTES"
echo "CONTAINER_MARKET_BYTES=$CONTAINER_MARKET_BYTES"
echo "MAIN_FILE=$MAIN_FILE"
echo "APP_ROUTES=$APP_ROUTES"
echo "MARKET_FILE=$MARKET_FILE"
echo "MARKET_ROUTES=$MARKET_ROUTES"
echo "MAIN_IMPORT_ERROR=$MAIN_IMPORT_ERROR"
echo "MARKET_IMPORT_ERROR=$MARKET_IMPORT_ERROR"

if [ "$CONTAINER_MAIN_BYTES" != "-1" ] && [ "$CONTAINER_MAIN_BYTES" != "0" ]; then
  add_check "container_main_py" "/app/app/main.py" "true" "size=$CONTAINER_MAIN_BYTES"
else
  add_check "container_main_py" "/app/app/main.py" "false" "missing or empty in container"
  add_error "main.py is missing or empty inside backend container."
fi

if [ "$CONTAINER_MARKET_BYTES" != "-1" ] && [ "$CONTAINER_MARKET_BYTES" != "0" ]; then
  add_check "container_market_data_py" "/app/app/api/market_data.py" "true" "size=$CONTAINER_MARKET_BYTES"
else
  add_check "container_market_data_py" "/app/app/api/market_data.py" "false" "missing or empty in container"
  add_error "market_data.py is missing or empty inside backend container."
fi

if [ -z "$MAIN_IMPORT_ERROR" ]; then
  add_check "main_import" "app.main" "true"
else
  add_check "main_import" "app.main" "false" "$MAIN_IMPORT_ERROR"
  add_error "Cannot import app.main: $MAIN_IMPORT_ERROR"
fi

if [ -z "$MARKET_IMPORT_ERROR" ]; then
  add_check "market_import" "app.api.market_data" "true"
else
  add_check "market_import" "app.api.market_data" "false" "$MARKET_IMPORT_ERROR"
  add_error "Cannot import app.api.market_data: $MARKET_IMPORT_ERROR"
fi

if printf '%s' "$MARKET_ROUTES" | grep -q "/api/instruments"; then
  add_check "market_router_routes" "market_data.router" "true"
  echo "OK: market_data.router contains /api/instruments"
else
  add_check "market_router_routes" "market_data.router" "false" "router does not contain /api/instruments"
  add_error "market_data.router does not contain /api/instruments."
fi

if printf '%s' "$APP_ROUTES" | grep -q "/api/instruments"; then
  add_check "app_routes" "app.main.app" "true"
  echo "OK: app contains /api/instruments"
else
  add_check "app_routes" "app.main.app" "false" "app does not contain /api/instruments"
  add_error "app does not contain /api/instruments."
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

MAIN_FILE_SAFE="$(sanitize "$MAIN_FILE")"
APP_ROUTES_SAFE="$(sanitize "$APP_ROUTES")"
MARKET_FILE_SAFE="$(sanitize "$MARKET_FILE")"
MARKET_ROUTES_SAFE="$(sanitize "$MARKET_ROUTES")"
MAIN_IMPORT_ERROR_SAFE="$(sanitize "$MAIN_IMPORT_ERROR")"
MARKET_IMPORT_ERROR_SAFE="$(sanitize "$MARKET_IMPORT_ERROR")"

cat > "$REPORT_JSON" <<EOF
{
  "task_id": "$TASK_ID",
  "status": "$STATUS",
  "started_at": "$STARTED_AT",
  "finished_at": "$FINISHED_AT",
  "environment": {
    "cwd": "$ROOT_DIR",
    "shell": "bash",
    "host_main_bytes": "$HOST_MAIN_BYTES",
    "host_market_bytes": "$HOST_MARKET_BYTES",
    "host_test_routes_bytes": "$HOST_TEST_ROUTES_BYTES",
    "container_main_bytes": "$CONTAINER_MAIN_BYTES",
    "container_market_bytes": "$CONTAINER_MARKET_BYTES",
    "main_file": "$MAIN_FILE_SAFE",
    "app_routes": "$APP_ROUTES_SAFE",
    "market_file": "$MARKET_FILE_SAFE",
    "market_routes": "$MARKET_ROUTES_SAFE",
    "main_import_error": "$MAIN_IMPORT_ERROR_SAFE",
    "market_import_error": "$MARKET_IMPORT_ERROR_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "diagnostic output"
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

Host main.py bytes: **$HOST_MAIN_BYTES**  
Host market_data.py bytes: **$HOST_MARKET_BYTES**  
Container main.py bytes: **$CONTAINER_MAIN_BYTES**  
Container market_data.py bytes: **$CONTAINER_MARKET_BYTES**

Main file: **$MAIN_FILE_SAFE**  
App routes: **$APP_ROUTES_SAFE**  
Market file: **$MARKET_FILE_SAFE**  
Market routes: **$MARKET_ROUTES_SAFE**  
Main import error: **$MAIN_IMPORT_ERROR_SAFE**  
Market import error: **$MARKET_IMPORT_ERROR_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
