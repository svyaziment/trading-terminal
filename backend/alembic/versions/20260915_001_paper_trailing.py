"""Add trailing stop columns to paper_positions

Revision ID: 20260915_001
Revises: 20260905_c9d2
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260915_001'
down_revision: Union[str, None] = '20260905_c9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add trailing stop columns to trading.paper_positions
    op.add_column('paper_positions', sa.Column('trailing_enabled', sa.Boolean(), nullable=False, server_default='false'), schema='trading')
    op.add_column('paper_positions', sa.Column('trailing_steps', sa.JSON(), nullable=True), schema='trading')
    op.add_column('paper_positions', sa.Column('risk_r', sa.Numeric(), nullable=True), schema='trading')
    op.add_column('paper_positions', sa.Column('current_stop_price', sa.Numeric(), nullable=True), schema='trading')
    op.add_column('paper_positions', sa.Column('step_reached', sa.Integer(), nullable=False, server_default='0'), schema='trading')
    
    # Initialize current_stop_price = stop_price for existing rows
    op.execute("""
        UPDATE trading.paper_positions
        SET current_stop_price = stop_price
        WHERE current_stop_price IS NULL
    """)


def downgrade() -> None:
    op.drop_column('paper_positions', 'step_reached', schema='trading')
    op.drop_column('paper_positions', 'current_stop_price', schema='trading')
    op.drop_column('paper_positions', 'risk_r', schema='trading')
    op.drop_column('paper_positions', 'trailing_steps', schema='trading')
    op.drop_column('paper_positions', 'trailing_enabled', schema='trading')
