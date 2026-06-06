"""add_reserved_to_stock_levels

Revision ID: 931ff6f51eec
Revises: 0e51cb14275b
Create Date: 2026-06-01 00:57:39.884561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '931ff6f51eec'
down_revision: Union[str, Sequence[str], None] = '0e51cb14275b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add reserved column to stock_levels."""
    op.add_column('stock_levels', sa.Column('reserved', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Remove reserved column."""
    op.drop_column('stock_levels', 'reserved')
