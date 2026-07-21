#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-023a-fix-broker-module"
ROOT_DIR="$(pwd)"
REPORT_DIR="$ROOT_DIR/reports/$TASK_ID"
LOG_FILE="$REPORT_DIR/log.txt"
REPORT_JSON="$REPORT_DIR/report.json"
REPORT_MD="$REPORT_DIR/report.md"

FEATURE_BRANCH="feat/broker-data-loader"

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

working_tree_acceptable() {
  local porcelain
  porcelain="$(git status --porcelain)"

  if [ -z "$porcelain" ]; then
    return 0
  fi

  local unexpected
  unexpected="$(printf '%s\n' "$porcelain" \
    | grep -v -E '^\?\? scripts/' \
    | grep -v -E '^\?\? backend/' \
    | grep -v -E '^ M backend/' \
    | grep -v -E '^M  backend/' \
    | grep -v -E '^A  backend/' \
    | grep -v -E '^ M docker-compose\.yml' \
    | grep -v -E '^M  docker-compose\.yml' \
    || true)"

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

if [ "$STATUS" = "success" ] && [ "$CURRENT_BRANCH" != "$FEATURE_BRANCH" ]; then
  echo "Trying to checkout $FEATURE_BRANCH..."

  if git show-ref --verify --quiet "refs/heads/$FEATURE_BRANCH"; then
    if git checkout "$FEATURE_BRANCH"; then
      CURRENT_BRANCH="$FEATURE_BRANCH"
      add_check "git_branch" "$FEATURE_BRANCH" "true"
      echo "OK: checked out $FEATURE_BRANCH"
    else
      add_check "git_branch" "$FEATURE_BRANCH" "false" "Cannot checkout feature branch" "needs_human"
      add_error "Cannot checkout $FEATURE_BRANCH."
      echo "FAIL: cannot checkout feature branch"
    fi
  else
    if git checkout -b "$FEATURE_BRANCH" main; then
      CURRENT_BRANCH="$FEATURE_BRANCH"
      add_check "git_branch" "$FEATURE_BRANCH" "true" "Created from main"
      echo "OK: created $FEATURE_BRANCH from main"
    else
      add_check "git_branch" "$FEATURE_BRANCH" "false" "Cannot create feature branch" "needs_human"
      add_error "Cannot create $FEATURE_BRANCH."
      echo "FAIL: cannot create feature branch"
    fi
  fi
else
  add_check "git_branch" "$FEATURE_BRANCH" "true"
  echo "OK: already on $FEATURE_BRANCH"
fi

echo "Checking working tree..."

if UNEXPECTED_CHANGES="$(working_tree_acceptable)"; then
  add_check "git_status_acceptable" "working tree" "true" "Clean or only expected changes"
  echo "OK: working tree is acceptable"
else
  add_check "git_status_acceptable" "working tree" "false" "Unexpected changes present" "needs_human"
  add_error "Unexpected changes in working tree. Commit or stash them before continuing."
  echo "FAIL: unexpected changes in working tree"
  echo "$UNEXPECTED_CHANGES" || true
fi

echo "Creating broker module..."

mkdir -p backend/app/broker

touch backend/app/broker/__init__.py

cat > backend/app/broker/data_loader.py <<'DATA_LOADER_EOF'
"""
Tinkoff / T-Bank Invest API data loader.

Loads candles using t-tech-investments SDK.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd

try:
    from t_tech.invest import Client, CandleInterval
    from t_tech.invest.constants import INVEST_GRPC_API

    IS_SDK_AVAILABLE = True
except ImportError:
    Client = None
    CandleInterval = None
    INVEST_GRPC_API = None
    IS_SDK_AVAILABLE = False


class DataLoader:
    """
    Loads market data from Tinkoff Invest API.
    """

    INTERVAL_MAP: Dict[str, object] = {}

    if IS_SDK_AVAILABLE:
        _INTERVAL_NAMES = {
            "1min": "CANDLE_INTERVAL_1_MIN",
            "5min": "CANDLE_INTERVAL_5_MIN",
            "15min": "CANDLE_INTERVAL_15_MIN",
            "30min": "CANDLE_INTERVAL_30_MINUTE",
            "hour": "CANDLE_INTERVAL_HOUR",
            "1h": "CANDLE_INTERVAL_HOUR",
            "day": "CANDLE_INTERVAL_DAY",
            "1d": "CANDLE_INTERVAL_DAY",
            "week": "CANDLE_INTERVAL_WEEK",
            "1w": "CANDLE_INTERVAL_WEEK",
            "month": "CANDLE_INTERVAL_MONTH",
            "1M": "CANDLE_INTERVAL_MONTH",
        }

        for key, value in _INTERVAL_NAMES.items():
            if hasattr(CandleInterval, value):
                INTERVAL_MAP[key] = getattr(CandleInterval, value)

    def __init__(self) -> None:
        self.token = os.getenv("TINVEST_TOKEN", "").strip()

    def fetch_candles_by_figi(
        self,
        figi: str,
        ticker: str = "",
        days: int = 7,
        interval_str: str = "30min",
    ) -> pd.DataFrame:
        """
        Fetch candles by FIGI.

        :param figi: instrument FIGI
        :param ticker: ticker for logging
        :param days: history days
        :param interval_str: interval, for example 30min, 1h, 1d
        :return: DataFrame with candles
        """

        if not IS_SDK_AVAILABLE:
            raise RuntimeError("t-tech-investments SDK is not available")

        if not self.token:
            raise RuntimeError("TINVEST_TOKEN is empty")

        interval = self.INTERVAL_MAP.get(interval_str)

        if interval is None:
            raise ValueError(f"Unknown interval: {interval_str}")

        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=days)

        with Client(self.token, target=INVEST_GRPC_API) as client:
            response = client.market_data.get_candles(
                figi=figi,
                from_=from_dt,
                to=now,
                interval=interval,
            )

            candles = getattr(response, "candles", []) or []

            rows = []

            for candle in candles:
                open_price = getattr(candle, "open", None)
                high_price = getattr(candle, "high", None)
                low_price = getattr(candle, "low", None)
                close_price = getattr(candle, "close", None)

                rows.append(
                    {
                        "time": getattr(candle, "time", None),
                        "open": float(open_price.units) + open_price.nano / 1e9 if open_price else None,
                        "high": float(high_price.units) + high_price.nano / 1e9 if high_price else None,
                        "low": float(low_price.units) + low_price.nano / 1e9 if low_price else None,
                        "close": float(close_price.units) + close_price.nano / 1e9 if close_price else None,
                        "volume": getattr(candle, "volume", None),
                    }
                )

            return pd.DataFrame(rows)
DATA_LOADER_EOF

cat > backend/app/broker/check_loader.py <<'CHECK_LOADER_EOF'
from app.broker.data_loader import DataLoader


def main() -> None:
    print("IMPORT_OK")

    loader = DataLoader()

    try:
        df = loader.fetch_candles_by_figi(
            figi="BBG004730ZJ9",
            ticker="VTBR",
            days=5,
            interval_str="30min",
        )

        print("ROWS=" + str(len(df)))

        if not df.empty:
            print("FIRST_TIME=" + str(df.iloc[0].get("time")))
            print("LAST_TIME=" + str(df.iloc[-1].get("time")))

    except Exception as exc:
        print("ROWS=0")
        print("ERROR_MESSAGE=" + str(exc))


if __name__ == "__main__":
    main()
CHECK_LOADER_EOF

echo "Checking files..."

for f in \
  backend/app/broker/__init__.py \
  backend/app/broker/data_loader.py \
  backend/app/broker/check_loader.py
do
  if [ -f "$f" ]; then
    add_check "file_exists" "$f" "true"
    echo "OK: file exists: $f"
  else
    add_check "file_exists" "$f" "false"
    echo "FAIL: file missing: $f"
  fi
done

echo "Ensuring t-tech-investments is in requirements..."

if ! grep -qxF "t-tech-investments" backend/requirements.txt; then
  echo "t-tech-investments" >> backend/requirements.txt
  echo "OK: t-tech-investments added to backend/requirements.txt"
else
  echo "OK: t-tech-investments already present"
fi

echo "Ensuring Dockerfile uses T-Bank private PyPI index..."

if ! grep -q "opensource.tbank.ru" backend/Dockerfile; then
  cat > backend/Dockerfile <<'DOCKERFILE_EOF'
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./

RUN pip install --no-cache-dir \
    --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple \
    --trusted-host opensource.tbank.ru \
    -r requirements-dev.txt

COPY app ./app
COPY tests ./tests

RUN find /app -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
RUN find /app -type f -name '*.py' -exec sed -i 's/\r$//' {} + 2>/dev/null || true

RUN python -c "import t_tech.invest; from t_tech.invest.constants import INVEST_GRPC_API; print('SDK_OK')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE_EOF

  echo "OK: backend/Dockerfile updated"
else
  echo "OK: backend/Dockerfile already uses private index"
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

IMPORT_OK=""
ROWS=""
ERROR_MESSAGE=""
FIRST_TIME=""
LAST_TIME=""

if [ "$STATUS" = "success" ]; then
  echo "Checking DataLoader import and candle loading..."

  if CHECK_OUTPUT="$(docker compose run --rm -T --no-deps backend python -m app.broker.check_loader 2>&1)"; then
    echo "----- BEGIN CHECK_OUTPUT -----"
    echo "$CHECK_OUTPUT"
    echo "----- END CHECK_OUTPUT -----"

    IMPORT_OK="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^IMPORT_OK$' | head -n1 || true)"
    ROWS="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^ROWS=' | head -n1 | cut -d'=' -f2- || true)"
    ERROR_MESSAGE="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^ERROR_MESSAGE=' | head -n1 | cut -d'=' -f2- || true)"
    FIRST_TIME="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^FIRST_TIME=' | head -n1 | cut -d'=' -f2- || true)"
    LAST_TIME="$(printf '%s\n' "$CHECK_OUTPUT" | tr -d '\r' | grep -E '^LAST_TIME=' | head -n1 | cut -d'=' -f2- || true)"

    if [ -n "$IMPORT_OK" ]; then
      add_check "dataloader_import" "app.broker.data_loader" "true"
      echo "OK: DataLoader import succeeded"
    else
      add_check "dataloader_import" "app.broker.data_loader" "false" "Import failed"
      add_error "DataLoader import failed."
      echo "FAIL: DataLoader import failed"
    fi

    if [ -n "$ERROR_MESSAGE" ]; then
      add_check "dataloader_fetch" "VTBR 30min" "false" "$ERROR_MESSAGE" "needs_human"
      add_error "DataLoader fetch failed: $ERROR_MESSAGE"
      echo "FAIL: DataLoader fetch failed: $ERROR_MESSAGE"
    elif [ -n "$ROWS" ] && [ "$ROWS" != "0" ]; then
      add_check "dataloader_fetch" "VTBR 30min" "true" "rows=$ROWS"
      echo "OK: loaded $ROWS candles"
    else
      add_check "dataloader_fetch" "VTBR 30min" "false" "No candles returned" "needs_human"
      add_error "No candles returned. Check token permissions, instrument FIGI, or market data availability."
      echo "FAIL: no candles returned"
    fi
  else
    add_check "dataloader_check_execution" "docker compose run" "false" "Check execution failed"
    add_error "DataLoader check execution failed."
    echo "FAIL: DataLoader check execution failed"
    echo "$CHECK_OUTPUT" || true
  fi
fi

COMMIT_CREATED="false"
COMMIT_SHA=""

if [ "$STATUS" = "success" ] || [ -n "$IMPORT_OK" ]; then
  echo "Staging broker module files..."

  git add backend/app/broker/ backend/Dockerfile backend/requirements.txt scripts/ 2>/dev/null || true

  add_check "git_add" "git add" "true"
  echo "OK: git add completed"
fi

if [ "$STATUS" = "success" ] || [ -n "$IMPORT_OK" ]; then
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

if [ "$STATUS" = "success" ] || [ -n "$IMPORT_OK" ]; then
  if git diff --cached --quiet; then
    add_check "git_commit" "commit" "true" "No changes to commit"
    echo "OK: no changes to commit"
  else
    echo "Creating commit..."

    if git commit -m "feat(task-023): add broker data_loader module"; then
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

if [ "$STATUS" = "success" ] || [ -n "$IMPORT_OK" ]; then
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

sanitize() {
  printf '%s' "$1" | tr -d '\r' | tr '\n' ' ' | tr -d '"' | tr -d '\\'
}

CURRENT_BRANCH_SAFE="$(sanitize "$CURRENT_BRANCH")"
COMMIT_SHA_SAFE="$(sanitize "$COMMIT_SHA")"
ROWS_SAFE="$(sanitize "$ROWS")"
ERROR_MESSAGE_SAFE="$(sanitize "$ERROR_MESSAGE")"
FIRST_TIME_SAFE="$(sanitize "$FIRST_TIME")"
LAST_TIME_SAFE="$(sanitize "$LAST_TIME")"

if [ -n "$IMPORT_OK" ]; then
  IMPORT_OK_JSON="true"
else
  IMPORT_OK_JSON="false"
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
    "feature_branch": "$FEATURE_BRANCH",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "import_ok": $IMPORT_OK_JSON,
    "rows": "$ROWS_SAFE",
    "first_time": "$FIRST_TIME_SAFE",
    "last_time": "$LAST_TIME_SAFE",
    "error_message": "$ERROR_MESSAGE_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/broker/__init__.py",
    "backend/app/broker/data_loader.py",
    "backend/app/broker/check_loader.py",
    "git commit"
  ],
  "errors": $ERRORS_JSON,
  "log_file": "reports/$TASK_ID/log.txt"
}
EOF

cat > "$REPORT_MD" <<EOF
# $TASK_ID

Status: **$STATUS**

Started: $STARTED_AT  
Finished: $FINISHED_AT

Feature branch: **$FEATURE_BRANCH**  
Current branch: **$CURRENT_BRANCH_SAFE**  
Import OK: **$IMPORT_OK_JSON**  
Rows loaded: **$ROWS_SAFE**  
First time: **$FIRST_TIME_SAFE**  
Last time: **$LAST_TIME_SAFE**  
Error message: **$ERROR_MESSAGE_SAFE**  
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
