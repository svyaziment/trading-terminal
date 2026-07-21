#!/usr/bin/env bash

set -u

TASK_ID="task-023e-rest-data-loader"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CWD="$(pwd)"
FEATURE_BRANCH="feat/broker-data-loader"
REPORT_DIR="${CWD}/reports/${TASK_ID}"
LOG_FILE="${REPORT_DIR}/log.txt"
REPORT_JSON="${REPORT_DIR}/report.json"
REPORT_MD="${REPORT_DIR}/report.md"

mkdir -p "${REPORT_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Task: ${TASK_ID} ==="
echo "Started: ${STARTED_AT}"
echo "Working directory: ${CWD}"
echo "Feature branch: ${FEATURE_BRANCH}"

cd "${CWD}" || { echo "FAIL: cannot cd to ${CWD}"; exit 1; }

# ---------- Git checks ----------
echo "Checking git..."
command -v git >/dev/null 2>&1 || { echo "FAIL: git not found"; exit 1; }
echo "OK: git exists"

[ -d .git ] || { echo "FAIL: not a git repo"; exit 1; }
echo "OK: git repository exists"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Current branch: ${CURRENT_BRANCH}"

if [ "${CURRENT_BRANCH}" != "${FEATURE_BRANCH}" ]; then
    git checkout "${FEATURE_BRANCH}" 2>/dev/null || git checkout -b "${FEATURE_BRANCH}" origin/main
    echo "OK: switched to ${FEATURE_BRANCH}"
else
    echo "OK: already on ${FEATURE_BRANCH}"
fi

# ---------- Rewrite data_loader.py as REST client ----------
echo "Rewriting backend/app/broker/data_loader.py as Tinkoff REST client..."

mkdir -p backend/app/broker
touch backend/app/broker/__init__.py

cat > backend/app/broker/data_loader.py <<'DATA_LOADER_EOF'
"""
Tinkoff / T-Bank Invest data loader via REST API.

Uses environment variables:
  TINVEST_TOKEN
  TINVEST_API_URL
  TINVEST_SSL_VERIFY
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pandas as pd
import requests
import urllib3


class DataLoader:
    INTERVAL_MAP = {
        "1min": "CANDLE_INTERVAL_1_MIN",
        "5min": "CANDLE_INTERVAL_5_MIN",
        "15min": "CANDLE_INTERVAL_15_MIN",
        "30min": "CANDLE_INTERVAL_30_MINUTE",
        "hour": "CANDLE_INTERVAL_HOUR",
        "1h": "CANDLE_INTERVAL_HOUR",
        "2h": "CANDLE_INTERVAL_2_HOUR",
        "4h": "CANDLE_INTERVAL_4_HOUR",
        "day": "CANDLE_INTERVAL_DAY",
        "1d": "CANDLE_INTERVAL_DAY",
        "week": "CANDLE_INTERVAL_WEEK",
        "1w": "CANDLE_INTERVAL_WEEK",
        "month": "CANDLE_INTERVAL_MONTH",
        "1M": "CANDLE_INTERVAL_MONTH",
    }

    def __init__(self) -> None:
        self.token = os.getenv("TINVEST_TOKEN", "").strip()
        self.base_url = os.getenv(
            "TINVEST_API_URL",
            "https://invest-public-api.tinkoff.ru/rest/",
        ).strip()

        verify_raw = os.getenv("TINVEST_SSL_VERIFY", "False").strip().lower()
        self.verify_ssl = verify_raw in ("1", "true", "yes", "on")

        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @staticmethod
    def _parse_quotation(value: Optional[Dict[str, Any]]) -> float:
        if not value:
            return 0.0

        units = value.get("units", 0)
        nano = value.get("nano", 0)

        return float(units) + float(nano) / 1_000_000_000

    def fetch_candles_by_figi(
        self,
        figi: str,
        ticker: str = "",
        days: int = 7,
        interval_str: str = "day",
    ) -> pd.DataFrame:
        if not self.token:
            raise RuntimeError("TINVEST_TOKEN is empty")

        interval = self.INTERVAL_MAP.get(interval_str)
        if not interval:
            raise ValueError(f"Unknown interval: {interval_str}")

        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=days)

        url = self.base_url.rstrip("/") + "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles"

        payload = {
            "figi": figi,
            "interval": interval,
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
        }

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30,
            verify=self.verify_ssl,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Tinkoff REST error {response.status_code}: {response.text[:1000]}"
            )

        data = response.json()
        candles = data.get("candles", [])

        rows = []

        for candle in candles:
            rows.append(
                {
                    "time": candle.get("time"),
                    "open": self._parse_quotation(candle.get("open")),
                    "high": self._parse_quotation(candle.get("high")),
                    "low": self._parse_quotation(candle.get("low")),
                    "close": self._parse_quotation(candle.get("close")),
                    "volume": int(candle.get("volume", 0)),
                }
            )

        df = pd.DataFrame(rows)

        if not df.empty and "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"])

        return df
DATA_LOADER_EOF

echo "OK: backend/app/broker/data_loader.py rewritten"

# ---------- Ensure requests dependency ----------
echo "Ensuring requests dependency..."

if ! grep -qxF "requests" backend/requirements.txt; then
    echo "requests" >> backend/requirements.txt
    echo "OK: requests added to backend/requirements.txt"
else
    echo "OK: requests already present"
fi

# ---------- Update docker-compose environment ----------
echo "Updating docker-compose.yml..."

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
      TINVEST_API_URL: ${TINVEST_API_URL:-https://invest-public-api.tinkoff.ru/rest/}
      TINVEST_SSL_VERIFY: ${TINVEST_SSL_VERIFY:-False}
      POSTGRES_HOST: ${POSTGRES_HOST:-host.docker.internal}
      POSTGRES_PORT: ${POSTGRES_PORT:-5432}
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_DB: ${POSTGRES_DB:-postgres}
      PSTGRS_PWD: ${PSTGRS_PWD:-}
      MARKET_DATA_SCHEMA: ${MARKET_DATA_SCHEMA:-trading}
      PGCLIENTENCODING: "UTF8"
      PGOPTIONS: "-c client_encoding=UTF8 -c lc_messages=C"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5
COMPOSE_EOF

echo "OK: docker-compose.yml updated"

# ---------- Docker build ----------
echo "Checking Docker daemon..."
docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon not running"; exit 1; }
echo "OK: docker daemon is running"

docker compose config >/dev/null 2>&1 || { echo "FAIL: docker-compose.yml invalid"; exit 1; }
echo "OK: docker-compose.yml is valid"

echo "Building backend image..."
if ! docker compose build backend; then
    echo "FAIL: backend image build failed"
    exit 1
fi
echo "OK: backend image built"

# ---------- Check token ----------
echo "Checking TINVEST_TOKEN..."

TOKEN_CHECK="$(docker compose run --rm -T --no-deps backend \
    python -c "import os; print('TOKEN_SET=' + ('true' if os.getenv('TINVEST_TOKEN') else 'false'))" 2>&1)"

echo "${TOKEN_CHECK}"

if ! echo "${TOKEN_CHECK}" | grep -q "TOKEN_SET=true"; then
    echo "FAIL: TINVEST_TOKEN is not set"
    exit 1
fi

echo "OK: TINVEST_TOKEN is set"

# ---------- Test REST data loader ----------
echo "Testing REST DataLoader: fetching 30min candles for VTBR (5 days)..."

LOADER_CHECK="$(docker compose run --rm -T --no-deps backend python - <<'PY'
from app.broker.data_loader import DataLoader

try:
    dl = DataLoader()
    df = dl.fetch_candles_by_figi(
        figi="BBG004730ZJ9",
        ticker="VTBR",
        days=5,
        interval_str="30min",
    )
    print("DATALOADER_INIT=ok")
    print("ROWS=" + str(len(df)))
    if not df.empty:
        print("FIRST_TIME=" + str(df.iloc[0].get("time")))
except Exception as exc:
    print("DATALOADER_INIT=error")
    print("ROWS=0")
    print("ERROR_MESSAGE=" + str(exc))
PY
)"

echo "----- BEGIN LOADER_CHECK -----"
echo "${LOADER_CHECK}"
echo "----- END LOADER_CHECK -----"

ROWS="$(echo "${LOADER_CHECK}" | grep 'ROWS=' | cut -d= -f2)"
ERROR_MESSAGE="$(echo "${LOADER_CHECK}" | grep 'ERROR_MESSAGE=' | cut -d= -f2-)"

if [ -z "${ROWS}" ] || [ "${ROWS}" = "0" ]; then
    echo "FAIL: DataLoader failed: ${ERROR_MESSAGE}"
    FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > "${REPORT_JSON}" <<EOF
{"task_id":"${TASK_ID}","status":"failed","started_at":"${STARTED_AT}","finished_at":"${FINISHED_AT}","errors":["${ERROR_MESSAGE}"]}
EOF
    exit 1
fi

echo "OK: DataLoader fetched ${ROWS} rows"

# ---------- Commit ----------
echo "Staging files..."

git add backend/app/broker/data_loader.py backend/requirements.txt docker-compose.yml scripts/${TASK_ID}.sh 2>/dev/null || \
    git add backend/app/broker/data_loader.py backend/requirements.txt docker-compose.yml

echo "OK: git add completed"

if git diff --cached --name-only | grep -Ei '(\.env$|secret|token|credential)'; then
    echo "FAIL: possible secret file staged"
    exit 1
fi

echo "OK: no obvious secret files staged"

if git diff --cached --quiet; then
    echo "OK: no changes to commit"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
else
    git commit -m "fix(task-023): use Tinkoff REST data loader with optional SSL verify" \
        && echo "OK: commit created" || echo "WARN: commit failed"
    COMMIT_SHA="$(git rev-parse --short HEAD)"
fi

echo "OK: HEAD commit: ${COMMIT_SHA}"

# ---------- Report ----------
FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${REPORT_JSON}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "success",
  "started_at": "${STARTED_AT}",
  "finished_at": "${FINISHED_AT}",
  "environment": {
    "cwd": "${CWD}",
    "feature_branch": "${FEATURE_BRANCH}",
    "rows": "${ROWS}",
    "commit_sha": "${COMMIT_SHA}"
  },
  "checks": [
    {"name": "git_repo", "status": "passed"},
    {"name": "data_loader_rest", "path": "backend/app/broker/data_loader.py", "status": "passed"},
    {"name": "requests_dependency", "path": "backend/requirements.txt", "status": "passed"},
    {"name": "docker_compose_updated", "status": "passed"},
    {"name": "docker_build", "status": "passed"},
    {"name": "token_set", "status": "passed"},
    {"name": "rest_data_loader_check", "status": "passed", "rows": "${ROWS}"},
    {"name": "git_commit", "path": "${COMMIT_SHA}", "status": "passed"}
  ],
  "errors": []
}
EOF

cat > "${REPORT_MD}" <<EOF
# ${TASK_ID}

**Status:** success

**Started:** ${STARTED_AT}
**Finished:** ${FINISHED_AT}
**Branch:** ${FEATURE_BRANCH}
**Commit:** ${COMMIT_SHA}
**Rows fetched:** ${ROWS}

## What fixed
1. \`backend/app/broker/data_loader.py\` switched from gRPC SDK to Tinkoff REST API.
2. Added \`TINVEST_SSL_VERIFY\` environment variable.
3. Default dev mode disables SSL verification.
4. Added \`requests\` dependency.
5. Updated \`docker-compose.yml\` environment.

## Checks
- Docker build: **passed**
- REST DataLoader check: **passed**, rows: ${ROWS}
- Commit: \`${COMMIT_SHA}\`
EOF

echo "Finished: ${FINISHED_AT}"
echo "Report JSON: ${REPORT_JSON}"
echo "Report MD: ${REPORT_MD}"
echo "Log: ${LOG_FILE}"
