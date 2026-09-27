"""Live equity snapshots and risk-gate state (Issue #176)

Revision ID: 20260927_001
Revises: 20260916_001
Create Date: 2026-09-27

Adds the persistence required by the live equity risk gates of Epic #172
(task D):

1. Create ``trading.live_equity`` - one row per executor cycle with the broker
   account equity, its cash / market-value split, the peak equity of the
   current MSK trading day and the drawdown the entry gate is evaluated
   against.

   ``timestamp`` is intentionally ``TIMESTAMP WITHOUT TIME ZONE`` holding a
   naive MSK wall clock, exactly like ``trading.paper_equity.timestamp`` and
   ``trading.live_positions.signal_ts``: the executor writes
   ``_now_msk_naive()`` everywhere, so a ``WITH TIME ZONE`` column would force
   a conversion in the writer and in every consumer.

   ``session_key`` is the MSK calendar day the snapshot belongs to. It is what
   makes ``max_daily_loss_pct`` a *daily* limit: ``peak_equity_rub`` is the
   peak within ``session_key`` only, while ``peak_equity_all_time_rub`` is
   kept separately for monitoring. Without the split the gate would compare a
   daily threshold against an all-time drawdown and could block entries
   forever.

2. Seed ``live_risk_breach_reset`` in ``trading.app_settings`` - the manual
   breach reset switch. It follows the ``trailing_kill_switch`` pattern from
   ``20260916_001``: the operator sets it to ``true``, the executor consumes
   it once and writes ``false`` back.

Idempotent: ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` /
``ON CONFLICT DO NOTHING``, so re-running on a database that already has the
objects is safe.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260927_001'
down_revision: Union[str, None] = '20260916_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CREATE_LIVE_EQUITY_SQL = """
CREATE TABLE IF NOT EXISTS trading.live_equity (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    session_key DATE NOT NULL,
    equity_rub NUMERIC(20, 4) NOT NULL,
    cash_rub NUMERIC(20, 4),
    market_value_rub NUMERIC(20, 4),
    realized_pnl_rub NUMERIC(20, 4),
    unrealized_pnl_rub NUMERIC(20, 4),
    peak_equity_rub NUMERIC(20, 4),
    peak_equity_all_time_rub NUMERIC(20, 4),
    drawdown_pct NUMERIC(10, 4),
    open_positions INTEGER,
    risk_breach BOOLEAN NOT NULL DEFAULT false,
    account_id VARCHAR(64),
    strategy_name TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_LIVE_EQUITY_TS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_equity_timestamp
ON trading.live_equity (timestamp DESC)
"""

_CREATE_LIVE_EQUITY_SESSION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_live_equity_session
ON trading.live_equity (session_key, timestamp DESC)
"""

_SEED_RISK_SETTINGS_SQL = """
INSERT INTO trading.app_settings (key, value, updated_at)
VALUES
    ('live_risk_breach_reset', 'false'::jsonb, CURRENT_TIMESTAMP)
ON CONFLICT (key) DO NOTHING
"""


def upgrade() -> None:
    # 1. Live equity snapshots (idempotent).
    op.execute(_CREATE_LIVE_EQUITY_SQL)
    op.execute(_CREATE_LIVE_EQUITY_TS_INDEX_SQL)
    op.execute(_CREATE_LIVE_EQUITY_SESSION_INDEX_SQL)

    # 2. Manual breach-reset switch (idempotent seed).
    op.execute(_SEED_RISK_SETTINGS_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS trading.idx_live_equity_session")
    op.execute("DROP INDEX IF EXISTS trading.idx_live_equity_timestamp")
    op.execute("DROP TABLE IF EXISTS trading.live_equity")
    op.execute(
        "DELETE FROM trading.app_settings WHERE key = 'live_risk_breach_reset'"
    )
