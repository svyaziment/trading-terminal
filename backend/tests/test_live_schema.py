"""Tests for the live-trading schema contract (Issue #173).

The runtime DDL must converge to the Alembic shape (30 columns, seven-value
status CHECK, ``trading.app_settings``) and drift must be detected fail-fast
before the executor starts trading.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from app.analytics.live_executor import LiveExecutor, ensure_live_positions_table
from app.analytics.live_schema import (
    ACTIVE_INDEX_NAME,
    APP_SETTINGS_TABLE,
    LIVE_POSITIONS_TABLE,
    LIVE_SCHEMA_STATEMENTS,
    REQUIRED_APP_SETTINGS_KEYS,
    REQUIRED_LIVE_POSITIONS_COLUMNS,
    REQUIRED_LIVE_POSITIONS_STATUSES,
    STATUS_CHECK_NAME,
    LiveSchemaError,
    assert_live_schema,
    describe_live_schema_problems,
    ensure_live_positions_schema,
    inspect_live_schema,
    live_schema_summary,
    validate_live_schema,
)


LEGACY_STATUSES = ("pending", "open", "closed_stop", "closed_take", "cancelled")


class Result:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else pd.DataFrame()

    def to_dataframe(self):
        return self.frame.copy()


class SchemaFakeDB:
    """Serves information_schema / pg_constraint answers from a fake catalog."""

    def __init__(
        self,
        *,
        columns=None,
        statuses=None,
        tables=None,
        app_settings_keys=None,
        fail_on=None,
    ):
        self.columns = (
            list(REQUIRED_LIVE_POSITIONS_COLUMNS)
            if columns is None
            else list(columns)
        )
        self.statuses = (
            list(REQUIRED_LIVE_POSITIONS_STATUSES)
            if statuses is None
            else list(statuses)
        )
        self.tables = (
            ["app_settings", "live_positions"] if tables is None else list(tables)
        )
        self.app_settings_keys = (
            list(REQUIRED_APP_SETTINGS_KEYS)
            if app_settings_keys is None
            else list(app_settings_keys)
        )
        self.fail_on = set(fail_on or ())
        self.execute_calls = []
        self.select_calls = []

    def execute(self, query, params=None):
        self.execute_calls.append((" ".join(query.split()), params))
        return 1

    def select(self, query, params=None):
        normalized = " ".join(query.split())
        self.select_calls.append((normalized, params))
        for marker in self.fail_on:
            if marker in normalized:
                raise RuntimeError(f"simulated failure on {marker}")
        if "information_schema.tables" in normalized:
            return Result(pd.DataFrame({"table_name": self.tables}))
        if "information_schema.columns" in normalized:
            return Result(pd.DataFrame({"column_name": self.columns}))
        if "pg_constraint" in normalized:
            if not self.statuses:
                return Result()
            definition = (
                "CHECK (((status)::text = ANY ((ARRAY["
                + ", ".join(
                    f"'{status}'::character varying" for status in self.statuses
                )
                + "])::text[])))"
            )
            return Result(pd.DataFrame({"definition": [definition]}))
        if "FROM trading.app_settings" in normalized:
            return Result(pd.DataFrame({"key": self.app_settings_keys}))
        return Result()


def test_contract_covers_the_migrated_thirty_column_shape():
    assert len(REQUIRED_LIVE_POSITIONS_COLUMNS) == 30
    assert len(set(REQUIRED_LIVE_POSITIONS_COLUMNS)) == 30
    assert REQUIRED_LIVE_POSITIONS_STATUSES == (
        "pending",
        "open",
        "closed_stop",
        "closed_take",
        "closed_trailing",
        "closed_broker",
        "cancelled",
    )
    for column in (
        "trailing_enabled",
        "trailing_steps",
        "risk_r",
        "current_stop_price",
        "step_reached",
        "exit_price_model",
        "exit_price_actual",
        "slippage_bp",
        "slippage_r",
        "lots_executed",
    ):
        assert column in REQUIRED_LIVE_POSITIONS_COLUMNS


def test_runtime_ddl_declares_every_contract_column():
    create_table = next(
        statement
        for statement in LIVE_SCHEMA_STATEMENTS
        if f"CREATE TABLE IF NOT EXISTS {LIVE_POSITIONS_TABLE}" in statement
    )
    for column in REQUIRED_LIVE_POSITIONS_COLUMNS:
        assert re.search(rf"\b{column}\b", create_table), column


def test_runtime_ddl_widens_status_check_to_seven_statuses():
    constraint_statements = [
        statement
        for statement in LIVE_SCHEMA_STATEMENTS
        if STATUS_CHECK_NAME in statement
    ]
    assert any("DROP CONSTRAINT IF EXISTS" in item for item in constraint_statements)
    add_statement = next(
        item for item in constraint_statements if "ADD CONSTRAINT" in item
    )
    for status in REQUIRED_LIVE_POSITIONS_STATUSES:
        assert f"'{status}'" in add_statement


def test_runtime_ddl_creates_app_settings_and_seeds_runtime_switches():
    joined = "\n".join(LIVE_SCHEMA_STATEMENTS)
    assert f"CREATE TABLE IF NOT EXISTS {APP_SETTINGS_TABLE}" in joined
    assert "value JSONB NOT NULL" in joined
    for key in REQUIRED_APP_SETTINGS_KEYS:
        assert f"'{key}'" in joined


def test_runtime_ddl_keeps_historical_statement_positions():
    statements = [
        " ".join(statement.split()) for statement in LIVE_SCHEMA_STATEMENTS
    ]
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS trading"
    assert f"CREATE TABLE IF NOT EXISTS {LIVE_POSITIONS_TABLE}" in statements[1]
    assert f"CREATE INDEX IF NOT EXISTS {ACTIVE_INDEX_NAME}" in statements[2]


def test_runtime_ddl_is_idempotent_and_re_runnable():
    db = SchemaFakeDB()

    ensure_live_positions_schema(db)
    first_run = list(db.execute_calls)
    db.execute_calls.clear()
    ensure_live_positions_schema(db)

    assert db.execute_calls == first_run
    joined = "\n".join(query for query, _ in first_run)
    assert "CREATE TABLE IF NOT EXISTS" in joined
    assert "ADD COLUMN IF NOT EXISTS" in joined
    assert "DROP CONSTRAINT IF EXISTS" in joined
    assert "ON CONFLICT (key) DO NOTHING" in joined
    assert "DROP TABLE" not in joined
    assert "DROP COLUMN" not in joined


def test_runtime_ddl_backfills_current_stop_price_like_the_migration():
    joined = "\n".join(LIVE_SCHEMA_STATEMENTS)
    assert "SET current_stop_price = stop_price" in joined
    assert "WHERE current_stop_price IS NULL" in joined


def test_ensure_live_positions_table_wrapper_delegates_to_the_contract():
    db = SchemaFakeDB()

    ensure_live_positions_table(db)

    statements = [query for query, _ in db.execute_calls]
    assert len(statements) == len(LIVE_SCHEMA_STATEMENTS)
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS trading"
    assert f"CREATE TABLE IF NOT EXISTS {LIVE_POSITIONS_TABLE}" in statements[1]
    assert f"CREATE INDEX IF NOT EXISTS {ACTIVE_INDEX_NAME}" in statements[2]


# --- inspection / validation ------------------------------------------------


def test_inspect_reads_the_actual_catalog_state():
    snapshot = inspect_live_schema(SchemaFakeDB(statuses=LEGACY_STATUSES))

    assert snapshot.tables == {"app_settings", "live_positions"}
    assert snapshot.columns == list(REQUIRED_LIVE_POSITIONS_COLUMNS)
    assert snapshot.statuses == set(LEGACY_STATUSES)
    assert snapshot.app_settings_keys == set(REQUIRED_APP_SETTINGS_KEYS)
    assert snapshot.inspection_errors == []


def test_validate_accepts_a_fully_migrated_schema():
    validation = validate_live_schema(SchemaFakeDB())

    assert validation.ok
    assert validation.errors == []
    assert len(validation.snapshot.columns) == 30


def test_validate_reports_missing_trailing_and_slippage_columns():
    narrow = list(REQUIRED_LIVE_POSITIONS_COLUMNS[:20])

    validation = validate_live_schema(SchemaFakeDB(columns=narrow))

    assert not validation.ok
    message = describe_live_schema_problems(validation)
    for column in ("current_stop_price", "step_reached", "slippage_bp", "lots_executed"):
        assert column in message
    assert "alembic upgrade head" in message


def test_validate_reports_a_legacy_status_check():
    validation = validate_live_schema(SchemaFakeDB(statuses=LEGACY_STATUSES))

    assert not validation.ok
    summary = live_schema_summary(validation)
    assert set(summary["missing_statuses"]) == {"closed_trailing", "closed_broker"}


def test_validate_reports_a_missing_status_check():
    validation = validate_live_schema(SchemaFakeDB(statuses=[]))

    assert not validation.ok
    assert any(STATUS_CHECK_NAME in error for error in validation.errors)


def test_validate_reports_missing_live_positions_table():
    validation = validate_live_schema(SchemaFakeDB(tables=["app_settings"]))

    assert not validation.ok
    assert any("live_positions is missing" in error for error in validation.errors)


def test_validate_reports_missing_app_settings_table():
    validation = validate_live_schema(SchemaFakeDB(tables=["live_positions"]))

    assert not validation.ok
    assert any("app_settings is missing" in error for error in validation.errors)


def test_missing_app_settings_keys_are_a_warning_only():
    validation = validate_live_schema(SchemaFakeDB(app_settings_keys=[]))

    assert validation.ok
    assert any("missing keys" in warning for warning in validation.warnings)


def test_extra_columns_are_a_warning_only():
    columns = list(REQUIRED_LIVE_POSITIONS_COLUMNS) + ["future_column"]

    validation = validate_live_schema(SchemaFakeDB(columns=columns))

    assert validation.ok
    assert any("future_column" in warning for warning in validation.warnings)


def test_inspection_failure_becomes_a_blocking_error():
    db = SchemaFakeDB(fail_on=["information_schema.columns"])

    validation = validate_live_schema(db)

    assert not validation.ok
    assert any(
        "cannot read information_schema.columns" in error
        for error in validation.errors
    )


def test_assert_live_schema_raises_with_an_actionable_message():
    with pytest.raises(LiveSchemaError) as excinfo:
        assert_live_schema(SchemaFakeDB(statuses=["pending", "open"]))

    message = str(excinfo.value)
    assert "closed_trailing" in message
    assert "alembic upgrade head" in message


def test_assert_live_schema_returns_the_validation_when_aligned():
    validation = assert_live_schema(SchemaFakeDB())

    assert validation.ok
    assert len(validation.snapshot.columns) == 30


def test_live_schema_summary_lists_every_missing_piece():
    summary = live_schema_summary(
        validate_live_schema(
            SchemaFakeDB(
                columns=list(REQUIRED_LIVE_POSITIONS_COLUMNS[:20]),
                statuses=LEGACY_STATUSES,
            )
        )
    )

    assert summary["ok"] is False
    assert summary["columns_found"] == 20
    assert summary["columns_required"] == 30
    assert "trailing_steps" in summary["missing_columns"]
    assert "closed_broker" in summary["missing_statuses"]
    assert summary["errors"]
    assert isinstance(summary["tables_found"], list)


# --- executor integration ---------------------------------------------------


EXECUTOR_CONFIG = {
    "enabled": True,
    "api_rate_limit": 10,
    "max_open_positions": 5,
    "imbalance_threshold": 1.0,
    "risk_per_trade_pct": 1.0,
    "max_position_pct": 20.0,
}


def test_executor_initialize_fails_fast_on_schema_drift():
    db = SchemaFakeDB(columns=list(REQUIRED_LIVE_POSITIONS_COLUMNS[:20]))
    executor = LiveExecutor(
        db=db, broker=object(), config=dict(EXECUTOR_CONFIG)
    )

    with pytest.raises(LiveSchemaError):
        executor.initialize()


def test_executor_initialize_passes_the_schema_gate_when_aligned():
    db = SchemaFakeDB()
    executor = LiveExecutor(
        db=db, broker=object(), config=dict(EXECUTOR_CONFIG)
    )

    with pytest.raises(RuntimeError) as excinfo:
        executor.initialize()

    # The schema gate passed; the failure comes from the (absent) strategy.
    assert not isinstance(excinfo.value, LiveSchemaError)
    assert "strategy" in str(excinfo.value).lower()
