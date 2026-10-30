"""Add live_executor_events table

Revision ID: 20261026_001
Revises: 20261025_001
Create Date: 2026-10-26 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = '20261026_001'
down_revision = '20261025_001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'live_executor_events',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('level', sa.String(length=10), nullable=False), # INFO, WARNING, ERROR, CRITICAL
        sa.Column('source', sa.String(length=50), nullable=False), # e.g., 'kill_switch', 'broker_client', 'signal_processor'
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', JSONB(), nullable=True),
    )
    
    # Index for fast retrieval of recent errors/warnings
    op.create_index(
        'idx_live_executor_events_ts_level',
        'live_executor_events',
        ['timestamp', 'level'],
        unique=False
    )
    
    # Index for filtering by source component
    op.create_index(
        'idx_live_executor_events_source',
        'live_executor_events',
        ['source'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('idx_live_executor_events_source', table_name='live_executor_events')
    op.drop_index('idx_live_executor_events_ts_level', table_name='live_executor_events')
    op.drop_table('live_executor_events')
