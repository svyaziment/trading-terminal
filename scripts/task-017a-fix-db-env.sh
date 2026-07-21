#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-017a-fix-db-env"
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

get_env_var() {
  local key="$1"

  if [ ! -f ".env" ]; then
    echo ""
    return 0
  fi

  grep -E "^${key}=" .env \
    | tail -n1 \
    | cut -d'=' -f2- \
    | tr -d '\r' \
    | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true
}

set_env_var() {
  local key="$1"
  local value="$2"

  if grep -Eq "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
    echo "OK: updated ${key}"
  else
    echo "${key}=${value}" >> .env
    echo "OK: added ${key}"
  fi
}

echo "Checking .env..."

if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    add_check "env_file" ".env" "true" "Created from .env.example"
    echo "OK: .env created from .env.example"
  else
    touch .env
    add_check "env_file" ".env" "true" "Created empty .env"
    echo "OK: empty .env created"
  fi
else
  add_check "env_file" ".env" "true" "Already exists"
  echo "OK: .env already exists"
fi

CURRENT_HOST="$(get_env_var POSTGRES_HOST)"
echo "Current POSTGRES_HOST: $CURRENT_HOST"

if [ -z "$CURRENT_HOST" ] || [ "$CURRENT_HOST" = "postgres" ]; then
  set_env_var "POSTGRES_HOST" "host.docker.internal"
  add_check "postgres_host_fixed" "POSTGRES_HOST" "true" "Set to host.docker.internal"
else
  add_check "postgres_host_fixed" "POSTGRES_HOST" "true" "Already not postgres"
fi

if [ -z "$(get_env_var POSTGRES_PORT)" ]; then
  set_env_var "POSTGRES_PORT" "5432"
fi

if [ -z "$(get_env_var POSTGRES_USER)" ]; then
  set_env_var "POSTGRES_USER" "postgres"
fi

if [ -z "$(get_env_var POSTGRES_DB)" ]; then
  set_env_var "POSTGRES_DB" "postgres"
fi

if [ -z "$(get_env_var MARKET_DATA_SCHEMA)" ]; then
  set_env_var "MARKET_DATA_SCHEMA" "trading"
fi

echo "Sanitized .env content:"
grep -E '^(POSTGRES_HOST|POSTGRES_PORT|POSTGRES_USER|POSTGRES_DB|MARKET_DATA_SCHEMA)=' .env || true

if grep -E '^PSTGRS_PWD=' .env >/dev/null 2>&1; then
  echo "OK: PSTGRS_PWD line exists"
else
  echo "WARNING: PSTGRS_PWD line not found"
fi

echo "Creating network check module..."

mkdir -p backend/app/db

if [ ! -f "backend/app/db/__init__.py" ]; then
  touch backend/app/db/__init__.py
fi

cat > backend/app/db/check_network.py <<'CHECK_NETWORK_EOF'
import os
import socket
import sys


def main() -> None:
    host = os.getenv("POSTGRES_HOST", "").strip()
    port_raw = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"

    try:
        port = int(port_raw)
    except ValueError:
        port = 5432

    user = os.getenv("POSTGRES_USER", "").strip()
    database = os.getenv("POSTGRES_DB", "").strip()
    password_set = bool(os.getenv("PSTGRS_PWD", "").strip())

    dns_ok = "false"
    tcp_ok = "false"
    error_message = ""

    if not host:
        error_message = "POSTGRES_HOST is empty"
    else:
        try:
            socket.getaddrinfo(host, port)
            dns_ok = "true"
        except Exception as exc:
            error_message = str(exc).replace("\n", " ")

        if dns_ok == "true":
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                tcp_ok = "true"
            except Exception as exc:
                if not error_message:
                    error_message = str(exc).replace("\n", " ")

    print("ENV_POSTGRES_HOST=" + host)
    print("ENV_POSTGRES_PORT=" + str(port))
    print("ENV_POSTGRES_USER=" + user)
    print("ENV_POSTGRES_DB=" + database)
    print("PSTGRS_PWD_SET=" + ("true" if password_set else "false"))
    print("DNS_OK=" + dns_ok)
    print("TCP_OK=" + tcp_ok)
    print("ERROR_MESSAGE=" + error_message)

    sys.exit(0)


if __name__ == "__main__":
    main()
CHECK_NETWORK_EOF

if [ -f "backend/app/db/check_network.py" ]; then
  add_check "file_exists" "backend/app/db/check_network.py" "true"
  echo "OK: file exists: backend/app/db/check_network.py"
else
  add_check "file_exists" "backend/app/db/check_network.py" "false"
  echo "FAIL: file missing: backend/app/db/check_network.py"
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

ENV_POSTGRES_HOST=""
ENV_POSTGRES_PORT=""
ENV_POSTGRES_USER=""
ENV_POSTGRES_DB=""
PSTGRS_PWD_SET=""
DNS_OK=""
TCP_OK=""
ERROR_MESSAGE=""

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
  echo "Running network check from backend container..."

  if DIAG_OUTPUT="$(docker compose run --rm -T backend python -m app.db.check_network 2>&1)"; then
    echo "$DIAG_OUTPUT"

    ENV_POSTGRES_HOST="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^ENV_POSTGRES_HOST=' | head -n1 | cut -d'=' -f2- || true)"
    ENV_POSTGRES_PORT="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^ENV_POSTGRES_PORT=' | head -n1 | cut -d'=' -f2- || true)"
    ENV_POSTGRES_USER="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^ENV_POSTGRES_USER=' | head -n1 | cut -d'=' -f2- || true)"
    ENV_POSTGRES_DB="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^ENV_POSTGRES_DB=' | head -n1 | cut -d'=' -f2- || true)"
    PSTGRS_PWD_SET="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^PSTGRS_PWD_SET=' | head -n1 | cut -d'=' -f2- || true)"
    DNS_OK="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^DNS_OK=' | head -n1 | cut -d'=' -f2- || true)"
    TCP_OK="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^TCP_OK=' | head -n1 | cut -d'=' -f2- || true)"
    ERROR_MESSAGE="$(printf '%s\n' "$DIAG_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"

    if [ "$PSTGRS_PWD_SET" = "true" ]; then
      add_check "pstgrs_pwd_set" ".env" "true"
      echo "OK: PSTGRS_PWD is set"
    else
      add_check "pstgrs_pwd_set" ".env" "false" "PSTGRS_PWD is not set" "needs_human"
      add_error "Set PSTGRS_PWD in .env and rerun."
      echo "FAIL: PSTGRS_PWD is not set"
    fi

    if [ "$DNS_OK" = "true" ]; then
      add_check "dns_ok" "$ENV_POSTGRES_HOST" "true"
      echo "OK: DNS resolves for $ENV_POSTGRES_HOST"
    else
      add_check "dns_ok" "$ENV_POSTGRES_HOST" "false" "$ERROR_MESSAGE" "needs_human"
      add_error "DNS failed for $ENV_POSTGRES_HOST: $ERROR_MESSAGE"
      echo "FAIL: DNS failed"
    fi

    if [ "$TCP_OK" = "true" ]; then
      add_check "tcp_ok" "$ENV_POSTGRES_HOST:$ENV_POSTGRES_PORT" "true"
      echo "OK: TCP connection to $ENV_POSTGRES_HOST:$ENV_POSTGRES_PORT succeeded"
    else
      add_check "tcp_ok" "$ENV_POSTGRES_HOST:$ENV_POSTGRES_PORT" "false" "$ERROR_MESSAGE" "needs_human"
      add_error "TCP connection failed to $ENV_POSTGRES_HOST:$ENV_POSTGRES_PORT: $ERROR_MESSAGE"
      echo "FAIL: TCP connection failed"
    fi
  else
    add_check "network_check_execution" "python -m app.db.check_network" "false" "Network check execution failed" "needs_human"
    add_error "Network check execution failed."
    echo "FAIL: network check execution failed"
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

ENV_POSTGRES_HOST_SAFE="$(printf '%s' "$ENV_POSTGRES_HOST" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
ENV_POSTGRES_PORT_SAFE="$(printf '%s' "$ENV_POSTGRES_PORT" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
ENV_POSTGRES_USER_SAFE="$(printf '%s' "$ENV_POSTGRES_USER" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
ENV_POSTGRES_DB_SAFE="$(printf '%s' "$ENV_POSTGRES_DB" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"
ERROR_MESSAGE_SAFE="$(printf '%s' "$ERROR_MESSAGE" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\')"

if [ "$PSTGRS_PWD_SET" != "true" ]; then
  PSTGRS_PWD_SET="false"
fi

if [ "$DNS_OK" != "true" ]; then
  DNS_OK="false"
fi

if [ "$TCP_OK" != "true" ]; then
  TCP_OK="false"
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
    "env_postgres_host": "$ENV_POSTGRES_HOST_SAFE",
    "env_postgres_port": "$ENV_POSTGRES_PORT_SAFE",
    "env_postgres_user": "$ENV_POSTGRES_USER_SAFE",
    "env_postgres_db": "$ENV_POSTGRES_DB_SAFE",
    "pstgrs_pwd_set": $PSTGRS_PWD_SET,
    "dns_ok": $DNS_OK,
    "tcp_ok": $TCP_OK,
    "error_message": "$ERROR_MESSAGE_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    ".env",
    "backend/app/db/check_network.py"
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

POSTGRES_HOST: **$ENV_POSTGRES_HOST_SAFE**  
POSTGRES_PORT: **$ENV_POSTGRES_PORT_SAFE**  
POSTGRES_USER: **$ENV_POSTGRES_USER_SAFE**  
POSTGRES_DB: **$ENV_POSTGRES_DB_SAFE**  
PSTGRS_PWD set: **$PSTGRS_PWD_SET**  
DNS OK: **$DNS_OK**  
TCP OK: **$TCP_OK**  
Error message: **$ERROR_MESSAGE_SAFE**

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
