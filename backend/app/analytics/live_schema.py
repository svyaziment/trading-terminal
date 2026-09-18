"""Schema contract for the live-trading runtime tables (Issue #173).

Alembic is the source of truth for ``trading.live_positions``:

* ``20260915_002_live_trailing`` adds the trailing-stop columns;
* ``20260916_001_live_trailing_runtime`` widens the status CHECK to seven
  values, adds the execution-fact columns and creates ``trading.app_settings``.

The runtime DDL in :func:`ensure_live_positions_schema` is an idempotent
superset of those migrations, so starting the executor standalone on a fresh
database converges to exactly the same shape (30 columns, 7 statuses).

:func:`validate_live_schema` compares the *actual* database state against that
contract and reports drift. Callers must treat errors as fatal: trading on a
narrow schema fails later with ``UndefinedColumn`` on the trailing fields or
with a CHECK violation when writing ``closed_trailing`` / ``closed_broker``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Set

logger = logging.getLogger(__name__)

LIVE_POSITIONS_TABLE = "trading.live_positions"
APP_SETTINGS_TABLE = "trading.app_settings"
STATUS_CHECK_NAME = "live_positions_status_check"
ACTIVE_INDEX_NAME = "idx_live_positions_active"

#: Columns required by the migration chain, in migration order (30 total).
REQUIRED_LIVE_POSITIONS_COLUMNS: tuple[str, ...] = (
    # Base table (runtime DDL / initial live schema).
    "id",
    "ticker",
    "instrument_id",
    "signal_ts",
    "entry_price",
    "lot_size",
    "size_lots",
    "stop_price",
    "take_price",
    "broker_order_id",
    "broker_stop_id",
    "broker_take_id",
    "status",
    "strategy_name",
    "exit_ts",
    "exit_price",
    "exit_reason",
    "pnl_rub",
    "created_at",
    "updated_at",
    # 20260915_002_live_trailing.
    "trailing_enabled",
    "trailing_steps",
    "risk_r",
    "current_stop_price",
    "step_reached",
    # 20260916_001_live_trailing_runtime.
    "exit_price_model",
    "exit_price_actual",
    "slippage_bp",
    "slippage_r",
    "lots_executed",
)

#: Statuses allowed by ``live_positions_status_check`` after 20260916_001.
REQUIRED_LIVE_POSITIONS_STATUSES: tuple[str, ...] = (
    "pending",
    "open",
    "closed_stop",
    "closed_take",
    "closed_trailing",
    "closed_broker",
    "cancelled",
)

#: ``trading.app_settings`` keys seeded by 20260916_001 and read every loop.
REQUIRED_APP_SETTINGS_KEYS: tuple[str, ...] = (
    "trailing_kill_switch",
    "live_trailing_enabled",
)

_STATUS_LIST_SQL = ", ".join(
    f"'{status}'" for status in REQUIRED_LIVE_POSITIONS_STATUSES
)

_CREATE_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS trading"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {LIVE_POSITIONS_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(32) NOT NULL,
    instrument_id VARCHAR(128) NOT NULL,
    signal_ts TIMESTAMP NOT NULL,
    entry_price NUMERIC(20, 9) NOT NULL,
    lot_size INTEGER NOT NULL CHECK (lot_size > 0),
    size_lots INTEGER NOT NULL CHECK (size_lots > 0),
    stop_price NUMERIC(20, 9) NOT NULL,
    take_price NUMERIC(20, 9) NOT NULL,
    broker_order_id VARCHAR(128) NOT NULL UNIQUE,
    broker_stop_id VARCHAR(128),
    broker_take_id VARCHAR(128),
    status VARCHAR(32) NOT NULL CHECK (status IN ({_STATUS_LIST_SQL})),
    strategy_name VARCHAR(255) NOT NULL,
    exit_ts TIMESTAMP,
    exit_price NUMERIC(20, 9),
    exit_reason VARCHAR(64),
    pnl_rub NUMERIC(20, 2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trailing_enabled BOOLEAN NOT NULL DEFAULT false,
    trailing_steps JSONB,
    risk_r NUMERIC,
    current_stop_price NUMERIC,
    step_reached INTEGER NOT NULL DEFAULT 0,
    exit_price_model NUMERIC(20, 9),
    exit_price_actual NUMERIC(20, 9),
    slippage_bp NUMERIC(12, 4),
    slippage_r NUMERIC(12, 6),
    lots_executed INTEGER
)
"""

_CREATE_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {ACTIVE_INDEX_NAME}
ON {LIVE_POSITIONS_TABLE} (status, ticker)
"""

# Convergence for databases created by the older 20-column runtime DDL:
# mirrors 20260915_002_live_trailing (columns + current_stop_price backfill).
_ADD_TRAILING_COLUMNS_SQL = f"""
ALTER TABLE {LIVE_POSITIONS_TABLE}
    ADD COLUMN IF NOT EXISTS trailing_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS trailing_steps JSONB,
    ADD COLUMN IF NOT EXISTS risk_r NUMERIC,
    ADD COLUMN IF NOT EXISTS current_stop_price NUMERIC,
    ADD COLUMN IF NOT EXISTS step_reached INTEGER NOT NULL DEFAULT 0
"""

_BACKFILL_CURRENT_STOP_SQL = f"""
UPDATE {LIVE_POSITIONS_TABLE}
SET current_stop_price = stop_price
WHERE current_stop_price IS NULL
"""

# Mirrors 20260916_001_live_trailing_runtime (execution-fact columns).
_ADD_EXIT_COLUMNS_SQL = f"""
ALTER TABLE {LIVE_POSITIONS_TABLE}
    ADD COLUMN IF NOT EXISTS exit_price_model NUMERIC(20, 9),
    ADD COLUMN IF NOT EXISTS exit_price_actual NUMERIC(20, 9),
    ADD COLUMN IF NOT EXISTS slippage_bp NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS slippage_r NUMERIC(12, 6),
    ADD COLUMN IF NOT EXISTS lots_executed INTEGER
"""

_DROP_STATUS_CHECK_SQL = (
    f"ALTER TABLE {LIVE_POSITIONS_TABLE} "
    f"DROP CONSTRAINT IF EXISTS {STATUS_CHECK_NAME}"
)

_ADD_STATUS_CHECK_SQL = f"""
ALTER TABLE {LIVE_POSITIONS_TABLE}
    ADD CONSTRAINT {STATUS_CHECK_NAME}
    CHECK (status IN ({_STATUS_LIST_SQL}))
"""

_CREATE_APP_SETTINGS_SQL = f"""
CREATE TABLE IF NOT EXISTS {APP_SETTINGS_TABLE} (
    key VARCHAR(64) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_SEED_APP_SETTINGS_SQL = f"""
INSERT INTO {APP_SETTINGS_TABLE} (key, value, updated_at)
VALUES
    ('trailing_kill_switch', 'false'::jsonb, CURRENT_TIMESTAMP),
    ('live_trailing_enabled', 'true'::jsonb, CURRENT_TIMESTAMP)
ON CONFLICT (key) DO NOTHING
"""

#: Ordered idempotent DDL. The first three statements keep their historical
#: positions (schema, table, index) because existing tests assert on them.
LIVE_SCHEMA_STATEMENTS: tuple[str, ...] = (
    _CREATE_SCHEMA_SQL,
    _CREATE_TABLE_SQL,
    _CREATE_INDEX_SQL,
    _ADD_TRAILING_COLUMNS_SQL,
    _BACKFILL_CURRENT_STOP_SQL,
    _ADD_EXIT_COLUMNS_SQL,
    _DROP_STATUS_CHECK_SQL,
    _ADD_STATUS_CHECK_SQL,
    _CREATE_APP_SETTINGS_SQL,
    _SEED_APP_SETTINGS_SQL,
)


def ensure_live_positions_schema(db: Any) -> None:
    """Apply the idempotent runtime form of the live-positions migrations.

    Mirrors ``20260915_002_live_trailing`` and ``20260916_001`` so that a
    standalone executor start on a fresh database converges to the migrated
    shape instead of the historical 20-column / 5-status table.
    """
    for statement in LIVE_SCHEMA_STATEMENTS:
        db.execute(statement)


_TABLES_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'trading'
  AND table_name IN ('live_positions', 'app_settings')
ORDER BY table_name
"""

_COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'trading' AND table_name = 'live_positions'
ORDER BY ordinal_position
"""

_STATUS_CHECK_SQL = f"""
SELECT pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'trading'
  AND t.relname = 'live_positions'
  AND c.conname = '{STATUS_CHECK_NAME}'
"""

_APP_SETTINGS_KEYS_SQL = f"SELECT key FROM {APP_SETTINGS_TABLE} ORDER BY key"


@dataclass
class LiveSchemaSnapshot:
    """Actual database state relevant to the live-trading contract."""

    tables: Set[str] = field(default_factory=set)
    columns: List[str] = field(default_factory=list)
    statuses: Set[str] = field(default_factory=set)
    app_settings_keys: Set[str] = field(default_factory=set)
    status_check_definition: str | None = None
    #: Inspection failures (e.g. no DB access); surfaced as validation errors.
    inspection_errors: List[str] = field(default_factory=list)


@dataclass
class LiveSchemaValidation:
    """Result of comparing the live schema against the contract."""

    snapshot: LiveSchemaSnapshot = field(default_factory=LiveSchemaSnapshot)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _fetch_frame(db: Any, sql: str) -> Any:
    return db.select(sql).to_dataframe()


def _string_column(frame: Any, name: str) -> List[str]:
    """Extract a string column from a (possibly empty) DataFrame."""
    if frame is None:
        return []
    try:
        if frame.empty or name not in frame.columns:
            return []
        return [str(value) for value in frame[name].tolist()]
    except AttributeError:
        return []


def _parse_allowed_statuses(definition: str) -> Set[str]:
    """Extract quoted literals from a ``pg_get_constraintdef`` payload."""
    return set(re.findall(r"'([^']+)'", definition or ""))


def inspect_live_schema(db: Any) -> LiveSchemaSnapshot:
    """Read the actual schema state; never raises for missing objects."""
    snapshot = LiveSchemaSnapshot()

    try:
        snapshot.tables = set(_string_column(_fetch_frame(db, _TABLES_SQL), "table_name"))
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        snapshot.inspection_errors.append(
            f"cannot read information_schema.tables: {type(exc).__name__}: {exc}"
        )

    try:
        snapshot.columns = _string_column(_fetch_frame(db, _COLUMNS_SQL), "column_name")
    except Exception as exc:  # noqa: BLE001
        snapshot.inspection_errors.append(
            f"cannot read information_schema.columns: {type(exc).__name__}: {exc}"
        )

    try:
        definitions = _string_column(_fetch_frame(db, _STATUS_CHECK_SQL), "definition")
        snapshot.status_check_definition = definitions[0] if definitions else None
        snapshot.statuses = (
            _parse_allowed_statuses(snapshot.status_check_definition)
            if snapshot.status_check_definition
            else set()
        )
    except Exception as exc:  # noqa: BLE001
        snapshot.inspection_errors.append(
            f"cannot read {STATUS_CHECK_NAME}: {type(exc).__name__}: {exc}"
        )

    try:
        snapshot.app_settings_keys = set(
            _string_column(_fetch_frame(db, _APP_SETTINGS_KEYS_SQL), "key")
        )
    except Exception as exc:  # noqa: BLE001
        snapshot.inspection_errors.append(
            f"cannot read {APP_SETTINGS_TABLE}: {type(exc).__name__}: {exc}"
        )

    return snapshot


class LiveSchemaError(RuntimeError):
    """Raised when the live-trading schema does not match the contract."""


_MIGRATION_HINT = (
    "Fix the schema with `alembic upgrade head` (run inside backend/, see "
    "docs/agents/handover.ru.md) and restart the executor."
)


def validate_live_schema(db: Any) -> LiveSchemaValidation:
    """Compare the actual schema against the contract and report drift."""
    snapshot = inspect_live_schema(db)
    validation = LiveSchemaValidation(snapshot=snapshot)
    validation.errors.extend(snapshot.inspection_errors)

    required_columns = set(REQUIRED_LIVE_POSITIONS_COLUMNS)
    actual_columns = set(snapshot.columns)

    if "live_positions" not in snapshot.tables:
        validation.errors.append("table trading.live_positions is missing")
    else:
        missing_columns = [
            column
            for column in REQUIRED_LIVE_POSITIONS_COLUMNS
            if column not in actual_columns
        ]
        if missing_columns:
            validation.errors.append(
                "trading.live_positions is missing columns: "
                + ", ".join(missing_columns)
            )
        extra_columns = sorted(actual_columns - required_columns)
        if extra_columns:
            validation.warnings.append(
                "trading.live_positions has columns outside the contract: "
                + ", ".join(extra_columns)
            )
        if not snapshot.statuses:
            validation.errors.append(
                f"CHECK constraint {STATUS_CHECK_NAME} is missing or unreadable"
            )
        else:
            missing_statuses = [
                status
                for status in REQUIRED_LIVE_POSITIONS_STATUSES
                if status not in snapshot.statuses
            ]
            if missing_statuses:
                validation.errors.append(
                    f"{STATUS_CHECK_NAME} does not allow statuses: "
                    + ", ".join(missing_statuses)
                )

    if "app_settings" not in snapshot.tables:
        validation.errors.append("table trading.app_settings is missing")
    else:
        missing_keys = [
            key
            for key in REQUIRED_APP_SETTINGS_KEYS
            if key not in snapshot.app_settings_keys
        ]
        if missing_keys:
            # Not fatal: the executor falls back to safe defaults, but the
            # operator must know the runtime switches are unavailable.
            validation.warnings.append(
                "trading.app_settings is missing keys: "
                + ", ".join(missing_keys)
                + " (executor falls back to defaults)"
            )

    return validation


def describe_live_schema_problems(validation: LiveSchemaValidation) -> str:
    """Render a human-readable, operator-actionable drift report."""
    lines = [
        f"live schema drift detected ({len(validation.errors)} blocking problem(s)):"
    ]
    lines.extend(f"  - {error}" for error in validation.errors)
    if validation.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in validation.warnings)
    lines.append(_MIGRATION_HINT)
    return "\n".join(lines)


def assert_live_schema(db: Any) -> LiveSchemaValidation:
    """Fail fast when the live schema drifted away from the migrations."""
    validation = validate_live_schema(db)
    if not validation.ok:
        message = describe_live_schema_problems(validation)
        logger.critical("%s", message)
        raise LiveSchemaError(message)
    for warning in validation.warnings:
        logger.warning("live schema: %s", warning)
    logger.info(
        "live schema OK: %d/%d columns, %d/%d statuses",
        len(validation.snapshot.columns),
        len(REQUIRED_LIVE_POSITIONS_COLUMNS),
        len(validation.snapshot.statuses),
        len(REQUIRED_LIVE_POSITIONS_STATUSES),
    )
    return validation


def live_schema_summary(validation: LiveSchemaValidation) -> dict[str, Any]:
    """Compact, JSON-serialisable view for preflight reports and APIs."""
    snapshot = validation.snapshot
    return {
        "ok": validation.ok,
        "errors": list(validation.errors),
        "warnings": list(validation.warnings),
        "tables_found": sorted(snapshot.tables),
        "columns_found": len(snapshot.columns),
        "columns_required": len(REQUIRED_LIVE_POSITIONS_COLUMNS),
        "missing_columns": [
            column
            for column in REQUIRED_LIVE_POSITIONS_COLUMNS
            if column not in set(snapshot.columns)
        ],
        "statuses_found": sorted(snapshot.statuses),
        "missing_statuses": [
            status
            for status in REQUIRED_LIVE_POSITIONS_STATUSES
            if status not in snapshot.statuses
        ],
        "app_settings_keys_found": sorted(snapshot.app_settings_keys),
    }
