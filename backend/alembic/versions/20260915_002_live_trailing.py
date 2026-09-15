"""Add trailing stop columns to live_positions (Issue #149 / #151)

Revision ID: 20260915_002
Revises: 20260915_001
Create Date: 2026-09-15

Idempotent: uses ADD COLUMN IF NOT EXISTS so re-running the migration on a DB
that already has the columns (e.g. via manual DDL) is safe.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260915_002'
down_revision: Union[str, None] = '20260915_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS_SQL = """
ALTER TABLE trading.live_positions
    ADD COLUMN IF NOT EXISTS trailing_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS trailing_steps JSONB,
    ADD COLUMN IF NOT EXISTS risk_r NUMERIC,
    ADD COLUMN IF NOT EXISTS current_stop_price NUMERIC,
    ADD COLUMN IF NOT EXISTS step_reached INTEGER NOT NULL DEFAULT 0
"""


def upgrade() -> None:
    # Idempotent DDL: safe to re-run on DBs where columns already exist.
    op.execute(_COLUMNS_SQL)

    # Backfill: for historical open positions that predate trailing,
    # initialize current_stop_price = stop_price so the engine can
    # immediately compare against the ladder.
    op.execute("""
        UPDATE trading.live_positions
        SET current_stop_price = stop_price
        WHERE current_stop_price IS NULL
    """)


def downgrade() -> None:
    op.drop_column('live_positions', 'step_reached', schema='trading')
    op.drop_column('live_positions', 'current_stop_price', schema='trading')
    op.drop_column('live_positions', 'risk_r', schema='trading')
    op.drop_column('live_positions', 'trailing_steps', schema='trading')
    op.drop_column('live_positions', 'trailing_enabled', schema='trading')