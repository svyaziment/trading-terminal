"""Live trailing runtime: extended status CHECK, exit columns, app_settings (Issue #151)

Revision ID: 20260916_001
Revises: 20260915_002
Create Date: 2026-09-16

Adds the runtime schema required for live trailing-stop ratchet, restart and
kill-switch behaviour:

1. Extend ``trading.live_positions.status`` CHECK to include ``closed_trailing``
   (the trailing exit status already referenced by the API and paper parity)
   and ``closed_broker`` (broker-side position disappearance).

2. Add execution-fact columns to ``trading.live_positions``:
   ``exit_price_model`` (model price, pre-#151 semantics),
   ``exit_price_actual`` (real fill price),
   ``slippage_bp`` (basis points), ``slippage_r`` (in R),
   ``lots_executed``.

3. Create ``trading.app_settings`` for runtime-toggleable switches
   (``trailing_kill_switch``, ``live_trailing_enabled``) read once per
   executor loop iteration.

Idempotent: uses ADD COLUMN IF NOT EXISTS, IF NOT EXISTS on CREATE TABLE,
and a conditional DROP/ADD CONSTRAINT (the constraint is dropped and
re-added each run, so the statement list converges to the same target).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260916_001'
down_revision: Union[str, None] = '20260915_002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_STATUSES = (
    "'pending','open','closed_stop','closed_take',"
    "'closed_trailing','closed_broker','cancelled'"
)

_EXTEND_STATUS_SQL = f"""
ALTER TABLE trading.live_positions DROP CONSTRAINT IF EXISTS live_positions_status_check;
ALTER TABLE trading.live_positions
    ADD CONSTRAINT live_positions_status_check
    CHECK (status IN ({_NEW_STATUSES}));
"""

_EXIT_COLUMNS_SQL = """
ALTER TABLE trading.live_positions
    ADD COLUMN IF NOT EXISTS exit_price_model NUMERIC(20, 9),
    ADD COLUMN IF NOT EXISTS exit_price_actual NUMERIC(20, 9),
    ADD COLUMN IF NOT EXISTS slippage_bp NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS slippage_r NUMERIC(12, 6),
    ADD COLUMN IF NOT EXISTS lots_executed INTEGER;
"""

_APP_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS trading.app_settings (
    key VARCHAR(64) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SEED_SETTINGS_SQL = """
INSERT INTO trading.app_settings (key, value, updated_at)
VALUES
    ('trailing_kill_switch', 'false'::jsonb, CURRENT_TIMESTAMP),
    ('live_trailing_enabled', 'true'::jsonb, CURRENT_TIMESTAMP)
ON CONFLICT (key) DO NOTHING;
"""


def upgrade() -> None:
    # 1. Extend CHECK constraint to include closed_trailing / closed_broker.
    op.execute(_EXTEND_STATUS_SQL)

    # 2. Execution-fact columns (idempotent).
    op.execute(_EXIT_COLUMNS_SQL)

    # 3. Runtime settings table + seed rows (idempotent).
    op.execute(_APP_SETTINGS_SQL)
    op.execute(_SEED_SETTINGS_SQL)


# Original CHECK values for downgrade.
_ORIGINAL_STATUSES = (
    "'pending','open','closed_stop','closed_take','cancelled'"
)


def downgrade() -> None:
    # Rewind closed_trailing / closed_broker to closed_stop so the narrower
    # CHECK constraint does not reject existing rows.
    op.execute("""
        UPDATE trading.live_positions
        SET status = 'closed_stop', exit_reason = 'stop'
        WHERE status IN ('closed_trailing', 'closed_broker')
    """)
    op.execute(f"""
        ALTER TABLE trading.live_positions DROP CONSTRAINT IF EXISTS live_positions_status_check;
        ALTER TABLE trading.live_positions
            ADD CONSTRAINT live_positions_status_check
            CHECK (status IN ({_ORIGINAL_STATUSES}));
    """)
    op.drop_column('live_positions', 'lots_executed', schema='trading')
    op.drop_column('live_positions', 'slippage_r', schema='trading')
    op.drop_column('live_positions', 'slippage_bp', schema='trading')
    op.drop_column('live_positions', 'exit_price_actual', schema='trading')
    op.drop_column('live_positions', 'exit_price_model', schema='trading')
    op.execute("DROP TABLE IF EXISTS trading.app_settings")