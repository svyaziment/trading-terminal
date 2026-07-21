#!/usr/bin/env bash

set -Eeuo pipefail

TASK_ID="task-016-config-db-manager"
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
    add_error "Run task-015-db-context first or checkout $EXPECTED_BRANCH."
    echo "FAIL: expected branch does not exist"
  fi
fi

echo "Creating config and DB manager files..."

mkdir -p backend/app/core
mkdir -p backend/app/db
mkdir -p backend/config

if [ ! -f "backend/app/core/__init__.py" ]; then
  touch backend/app/core/__init__.py
fi

if [ ! -f "backend/app/db/__init__.py" ]; then
  touch backend/app/db/__init__.py
fi

cat > backend/app/core/config_manager.py <<'CONFIG_MANAGER_EOF'
"""
Configuration manager for Trading Terminal backend.

Priority:
1. Environment variables
2. YAML config file
3. Default values

Database password is loaded only from environment variables.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "settings.yaml"


class BaseConfig(BaseModel):
    model_config = {"extra": "ignore"}


class ApiConfig(BaseConfig):
    token: str = ""
    account_id: str = ""


class TerminalConfig(BaseConfig):
    theme: str = "dark"
    refresh_rate_sec: int = 5


class RiskConfig(BaseConfig):
    max_daily_loss_pct: float = 2.0
    max_position_size: int = 100000


class DbConfig(BaseConfig):
    host: str = "localhost"
    database: str = "postgres"
    user: str = "postgres"
    password: str = ""
    port: int = 5432


class Settings(BaseConfig):
    api: ApiConfig = Field(default_factory=ApiConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    db: DbConfig = Field(default_factory=DbConfig)


def _load_yaml_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}

    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        return {}

    return data


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default

    value = value.strip()
    if value == "":
        return default

    return value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default

    try:
        return int(value.strip())
    except ValueError:
        return default


def load_settings() -> Settings:
    yaml_data = _load_yaml_config()

    api_yaml = yaml_data.get("api", {}) or {}
    terminal_yaml = yaml_data.get("terminal", {}) or {}
    risk_yaml = yaml_data.get("risk", {}) or {}
    db_yaml = yaml_data.get("db", {}) or {}

    api_config = ApiConfig(
        token=_env_str("TINVEST_TOKEN", str(api_yaml.get("token", ""))),
        account_id=_env_str("TINVEST_ACC", str(api_yaml.get("account_id", ""))),
    )

    terminal_config = TerminalConfig(**terminal_yaml)
    risk_config = RiskConfig(**risk_yaml)

    db_password = _env_str("PSTGRS_PWD", "")
    if db_password == "":
        db_password = _env_str("POSTGRES_PASSWORD", "")

    db_config = DbConfig(
        host=_env_str("POSTGRES_HOST", str(db_yaml.get("host", "localhost"))),
        database=_env_str("POSTGRES_DB", str(db_yaml.get("database", "postgres"))),
        user=_env_str("POSTGRES_USER", str(db_yaml.get("user", "postgres"))),
        password=db_password,
        port=_env_int("POSTGRES_PORT", int(db_yaml.get("port", 5432))),
    )

    return Settings(
        api=api_config,
        terminal=terminal_config,
        risk=risk_config,
        db=db_config,
    )


def get_db_password() -> str:
    password = _env_str("PSTGRS_PWD", "")
    if password == "":
        password = _env_str("POSTGRES_PASSWORD", "")
    return password


def setup_logger(name: str = "TradingTerminal") -> logging.Logger:
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        try:
            log_dir = ROOT_DIR / "logs_internal"
            log_dir.mkdir(exist_ok=True)

            file_handler = logging.FileHandler(
                log_dir / "app.log",
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass

    return logger
CONFIG_MANAGER_EOF

cat > backend/app/db/db_manager.py <<'DB_MANAGER_EOF'
"""
Synchronous PostgreSQL manager.

This module is intended for analytics, ETL and utility workloads.
For FastAPI request handling, prefer async database access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values

from app.core.config_manager import load_settings, setup_logger


logger = setup_logger("DBManager")


class SelectResult(dict):
    """SELECT result with DataFrame conversion support."""

    def to_dataframe(self) -> pd.DataFrame:
        results = self["data"]
        columns = self["columns"]
        types_df = self["types_df"]

        df = pd.DataFrame(results, columns=columns)

        for col, target_type in zip(columns, types_df):
            try:
                if "datetime64" in target_type:
                    df[col] = pd.to_datetime(df[col], utc=("UTC" in target_type))
                elif target_type in {"int64", "int32", "int16"}:
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif target_type in {"float64", "float32"}:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif target_type == "bool":
                    df[col] = df[col].astype(bool)
                elif target_type == "string":
                    df[col] = df[col].astype(str)
            except (ValueError, TypeError) as exc:
                if "Cannot convert non-finite values" not in str(exc):
                    logger.warning(
                        "Conversion error for column %s type %s: %s",
                        col,
                        target_type,
                        exc,
                    )
                continue

        return df


class DBManager:
    """PostgreSQL manager with connection pooling."""

    PG_TYPE_OID_TO_NAME = {
        20: {"pg": "int8", "df": "int64"},
        21: {"pg": "int2", "df": "int16"},
        23: {"pg": "int4", "df": "int32"},
        1700: {"pg": "numeric", "df": "float64"},
        701: {"pg": "float8", "df": "float64"},
        700: {"pg": "float4", "df": "float32"},
        25: {"pg": "text", "df": "string"},
        1043: {"pg": "varchar", "df": "string"},
        1042: {"pg": "bpchar", "df": "string"},
        1082: {"pg": "date", "df": "datetime64[ns]"},
        1114: {"pg": "timestamp", "df": "datetime64[ns]"},
        1184: {"pg": "timestamptz", "df": "datetime64[ns, UTC]"},
        1186: {"pg": "interval", "df": "timedelta64[ns]"},
        16: {"pg": "bool", "df": "bool"},
    }

    _connection_pool: Optional[pool.ThreadedConnectionPool] = None

    def __init__(self) -> None:
        self.settings = load_settings()
        self._last_result: Optional[Dict[str, Any]] = None

        self.conn_params = {
            "host": self.settings.db.host,
            "database": self.settings.db.database,
            "user": self.settings.db.user,
            "password": self.settings.db.password,
            "port": self.settings.db.port,
        }

        if DBManager._connection_pool is None:
            try:
                DBManager._connection_pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=20,
                    **self.conn_params,
                )
                logger.info("PostgreSQL connection pool created")
            except Exception as exc:
                logger.error("Cannot create PostgreSQL connection pool: %s", exc)
                raise

    def _get_conn(self):
        if DBManager._connection_pool is None:
            raise RuntimeError("Connection pool is not initialized")
        return DBManager._connection_pool.getconn()

    def _release_conn(self, conn) -> None:
        if DBManager._connection_pool is not None:
            DBManager._connection_pool.putconn(conn)

    def close_pool(self) -> None:
        if DBManager._connection_pool is not None:
            DBManager._connection_pool.closeall()
            DBManager._connection_pool = None
            logger.info("PostgreSQL connection pool closed")

    def select(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> SelectResult:
        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                if print_query:
                    logger.info("SQL QUERY:\n%s", query)
                    if params:
                        logger.info("PARAMS: %s", params)

                cursor.execute(query, params)

                if cursor.description is None:
                    self._last_result = {
                        "data": [[cursor.rowcount]],
                        "columns": ["affected_rows"],
                        "types": ["int8"],
                        "types_df": ["int64"],
                    }
                    return SelectResult(self._last_result)

                results = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]

                col_types = []
                col_types_df = []

                for desc in cursor.description:
                    type_info = self.PG_TYPE_OID_TO_NAME.get(
                        desc[1],
                        {"pg": "text", "df": "string"},
                    )
                    col_types.append(type_info["pg"])
                    col_types_df.append(type_info["df"])

                self._last_result = {
                    "data": results,
                    "columns": col_names,
                    "types": col_types,
                    "types_df": col_types_df,
                }

                return SelectResult(self._last_result)

        except Exception as exc:
            logger.error("SELECT error: %s", exc)
            raise
        finally:
            self._release_conn(conn)

    def execute(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> int:
        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                if print_query:
                    logger.info("SQL QUERY:\n%s", query)
                    if params:
                        logger.info("PARAMS: %s", params)

                cursor.execute(query, params)
                conn.commit()
                return cursor.rowcount

        except Exception as exc:
            conn.rollback()
            logger.error("EXECUTE error: %s", exc)
            raise
        finally:
            self._release_conn(conn)

    def create_table(
        self,
        table_name: str,
        columns: Dict[str, str],
        drop_if_exists: bool = False,
    ) -> None:
        if drop_if_exists:
            self.drop_table(table_name)

        columns = dict(columns)

        if "created_at" not in columns:
            columns["created_at"] = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

        column_defs = ", ".join(
            [f"{column} {dtype}" for column, dtype in columns.items()]
        )

        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({column_defs})"
        self.execute(query)
        logger.info("Table created: %s", table_name)

    def drop_table(self, table_name: str) -> None:
        query = f"DROP TABLE IF EXISTS {table_name} CASCADE"
        self.execute(query)
        logger.info("Table dropped: %s", table_name)

    def insert(
        self,
        table_name: str,
        data: Union[Dict[str, Any], pd.DataFrame, List[Any]],
        columns: Optional[List[str]] = None,
    ) -> None:
        if isinstance(data, pd.DataFrame):
            self.insert_with_schema(table_name, data)
            return

        if isinstance(data, dict):
            cols = list(data.keys())
            values = [tuple(data.values())]
        elif isinstance(data, list) and columns:
            cols = columns
            values = data
        else:
            raise ValueError("Unsupported data format")

        full_table_name = (
            table_name if "." in table_name else f"trading.{table_name}"
        )

        query = f"INSERT INTO {full_table_name} ({', '.join(cols)}) VALUES %s"

        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                execute_values(cursor, query, values)
                conn.commit()

            logger.info(
                "%s rows inserted into %s",
                len(values),
                full_table_name,
            )
        except Exception as exc:
            conn.rollback()
            logger.error("Insert error into %s: %s", full_table_name, exc)
            raise
        finally:
            self._release_conn(conn)

    def insert_with_schema(self, table_name: str, df: pd.DataFrame) -> None:
        if "." not in table_name:
            full_table_name = f"trading.{table_name}"
        else:
            full_table_name = table_name

        df = df.copy()

        if df.empty:
            logger.info("DataFrame is empty, nothing to insert into %s", full_table_name)
            return

        for col in df.columns:
            if pd.api.types.is_float_dtype(df[col]):
                df[col] = df[col].astype(float)
            elif pd.api.types.is_integer_dtype(df[col]):
                df[col] = df[col].astype(int)
            elif pd.api.types.is_bool_dtype(df[col]):
                df[col] = df[col].astype(bool)
            elif df[col].dtype == "object":
                df[col] = df[col].apply(
                    lambda x: x.item() if hasattr(x, "item") else x
                )

        cols = ", ".join(df.columns)
        values = [tuple(row) for row in df.itertuples(index=False, name=None)]

        query = f"INSERT INTO {full_table_name} ({cols}) VALUES %s"

        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                execute_values(cursor, query, values)
                conn.commit()

            logger.info(
                "%s rows inserted into %s",
                len(values),
                full_table_name,
            )
        except Exception as exc:
            conn.rollback()
            logger.error("Insert error into %s: %s", full_table_name, exc)
            raise
        finally:
            self._release_conn(conn)

    def get_column_types_for_postgres(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, str]:
        pandas_to_postgresql = {
            "int64": "BIGINT",
            "float64": "DOUBLE PRECISION",
            "bool": "BOOLEAN",
            "object": "TEXT",
            "datetime64[ns]": "TIMESTAMP",
            "timedelta64[ns]": "INTERVAL",
            "int32": "INTEGER",
            "uint32": "INTEGER",
            "int16": "SMALLINT",
            "uint16": "SMALLINT",
            "int8": "SMALLINT",
            "uint8": "SMALLINT",
            "float32": "REAL",
            "string": "TEXT",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "time": "TIME",
        }

        return {
            col_name: pandas_to_postgresql.get(str(dtype), "TEXT")
            for col_name, dtype in df.dtypes.items()
        }

    def execute_from_file(
        self,
        file_path: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> int:
        query = Path(file_path).read_text(encoding="utf-8")
        return self.execute(query, params=params, print_query=print_query)

    def select_from_file(
        self,
        file_path: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> SelectResult:
        query = Path(file_path).read_text(encoding="utf-8")
        return self.select(query, params=params, print_query=print_query)
DB_MANAGER_EOF

cat > backend/config/settings.yaml.example <<'SETTINGS_EXAMPLE_EOF'
api:
  token: ""
  account_id: ""

terminal:
  theme: "dark"
  refresh_rate_sec: 5

risk:
  max_daily_loss_pct: 2.0
  max_position_size: 100000

db:
  host: "localhost"
  database: "postgres"
  user: "postgres"
  port: 5432
SETTINGS_EXAMPLE_EOF

if [ ! -f "backend/config/settings.yaml" ]; then
  cp backend/config/settings.yaml.example backend/config/settings.yaml
  echo "OK: backend/config/settings.yaml created from example"
else
  echo "OK: backend/config/settings.yaml already exists"
fi

cat > backend/requirements.txt <<'REQ_EOF'
fastapi
uvicorn
pydantic
pyyaml
psycopg2-binary
pandas
asyncpg
REQ_EOF

cat > backend/requirements-dev.txt <<'REQ_DEV_EOF'
-r requirements.txt
pytest
httpx
REQ_DEV_EOF

cat > .env.example <<'ENV_EXAMPLE_EOF'
# Ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434

# Tinkoff / T-Bank Invest API
TINVEST_TOKEN=
TINVEST_ACC=

# PostgreSQL password.
# Password must be provided only via environment variables.
PSTGRS_PWD=

# PostgreSQL connection overrides.
# If PostgreSQL runs on your Windows host and backend runs in Docker,
# use host.docker.internal.
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_DB=postgres

# Optional explicit database URL for future async SQLAlchemy usage.
# Example:
# MARKET_DATA_DATABASE_URL=postgresql://postgres:PASSWORD@host.docker.internal:5432/postgres
MARKET_DATA_DATABASE_URL=

# Existing analytics schema used by your current database.
MARKET_DATA_SCHEMA=trading
ENV_EXAMPLE_EOF

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
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5
COMPOSE_EOF

echo "Updating .gitignore..."

touch .gitignore

for ignore_line in \
  "backend/config/settings.yaml" \
  "backend/logs_internal/"
do
  if ! grep -qxF "$ignore_line" .gitignore; then
    echo "$ignore_line" >> .gitignore
    echo "OK: added to .gitignore: $ignore_line"
  fi
done

echo "Creating tests..."

cat > backend/tests/test_config_manager.py <<'TEST_CONFIG_EOF'
from app.core.config_manager import load_settings


def test_load_settings_returns_settings_object() -> None:
    settings = load_settings()

    assert isinstance(settings.db.host, str)
    assert isinstance(settings.db.database, str)
    assert isinstance(settings.db.user, str)
    assert isinstance(settings.db.password, str)
    assert isinstance(settings.db.port, int)


def test_terminal_and_risk_defaults_are_loaded() -> None:
    settings = load_settings()

    assert settings.terminal.theme in {"dark", "light"}
    assert isinstance(settings.terminal.refresh_rate_sec, int)
    assert isinstance(settings.risk.max_daily_loss_pct, float)
    assert isinstance(settings.risk.max_position_size, int)
TEST_CONFIG_EOF

cat > backend/tests/test_db_manager.py <<'TEST_DB_MANAGER_EOF'
from app.db.db_manager import DBManager, SelectResult


def test_select_result_to_dataframe() -> None:
    result = SelectResult(
        {
            "data": [
                (1, "SBER", 12.5),
                (2, "GAZP", 113.77),
            ],
            "columns": ["id", "ticker", "price"],
            "types": ["int4", "varchar", "numeric"],
            "types_df": ["int32", "string", "float64"],
        }
    )

    df = result.to_dataframe()

    assert len(df) == 2
    assert list(df.columns) == ["id", "ticker", "price"]
    assert df.loc[0, "ticker"] == "SBER"
    assert df.loc[1, "ticker"] == "GAZP"
    assert float(df.loc[0, "price"]) == 12.5


def test_db_manager_class_has_expected_methods() -> None:
    assert hasattr(DBManager, "select")
    assert hasattr(DBManager, "execute")
    assert hasattr(DBManager, "insert")
    assert hasattr(DBManager, "insert_with_schema")
    assert hasattr(DBManager, "create_table")
    assert hasattr(DBManager, "drop_table")
TEST_DB_MANAGER_EOF

echo "Checking files..."

for f in \
  backend/app/core/config_manager.py \
  backend/app/db/db_manager.py \
  backend/config/settings.yaml.example \
  backend/config/settings.yaml \
  backend/requirements.txt \
  backend/requirements-dev.txt \
  backend/tests/test_config_manager.py \
  backend/tests/test_db_manager.py \
  .env.example \
  docker-compose.yml
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
  echo "This may take several minutes because pandas and psycopg2 are installed."

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

    if git commit -m "feat(task-016): add config manager and sync DB manager"; then
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
    "expected_branch": "$EXPECTED_BRANCH",
    "current_branch": "$CURRENT_BRANCH_SAFE",
    "commit_created": $COMMIT_CREATED,
    "commit_sha": "$COMMIT_SHA_SAFE"
  },
  "checks": [
$CHECKS_JSON
  ],
  "artifacts": [
    "backend/app/core/config_manager.py",
    "backend/app/db/db_manager.py",
    "backend/config/settings.yaml.example",
    "backend/requirements.txt",
    ".env.example",
    "docker-compose.yml",
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

## Checks
$CHECKS_MD

## Errors

$ERRORS_MD
EOF

echo "Finished: $FINISHED_AT"
echo "Report JSON: $REPORT_JSON"
echo "Report MD: $REPORT_MD"
echo "Log: $LOG_FILE"
