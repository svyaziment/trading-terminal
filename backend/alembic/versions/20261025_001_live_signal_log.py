"""live signal log

Revision ID: 20261025_001
Revises: 20260928_001
Create Date: 2026-10-24T19:34:05.863286+00:00
"""

from alembic import op


revision = "20261025_001"
down_revision = "20260928_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS trading;

        
CREATE TABLE IF NOT EXISTS trading.live_signal_log (
    id BIGSERIAL PRIMARY KEY,
    signal_ts TIMESTAMP NOT NULL,
    ticker VARCHAR(32) NOT NULL,
    contour VARCHAR(16) NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    reason VARCHAR(64) NOT NULL,
    entry_price NUMERIC(20, 9),
    stop_price NUMERIC(20, 9),
    take_price NUMERIC(20, 9),
    imbalance NUMERIC,
    size_lots INTEGER,
    position_id INTEGER,
    strategy_name VARCHAR(255),
    details JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

        """
    )

    op.execute(
        """
        
CREATE INDEX IF NOT EXISTS idx_live_signal_log_created
ON trading.live_signal_log (created_at DESC);

        """
    )

    op.execute(
        """
        
CREATE INDEX IF NOT EXISTS idx_live_signal_log_ticker_ts
ON trading.live_signal_log (ticker, signal_ts DESC);

        """
    )

    op.execute(
        """
        
CREATE INDEX IF NOT EXISTS idx_live_signal_log_outcome_reason
ON trading.live_signal_log (outcome, reason);

        """
    )


def downgrade() -> None:
    # Issue #216: this table stores audit/history data.
    # Downgrade is intentionally destructive only for fresh/local environments.
    # If real trading history already exists, do not downgrade without backup.
    op.execute("DROP TABLE IF EXISTS trading.live_signal_log")
