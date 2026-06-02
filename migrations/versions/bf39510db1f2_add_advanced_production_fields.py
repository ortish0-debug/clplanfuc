"""add_advanced_production_fields

Revision ID: bf39510db1f2
Revises: 888673a6a49e
Create Date: 2026-06-01 01:03:55.229918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf39510db1f2'
down_revision: Union[str, Sequence[str], None] = '888673a6a49e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add scrap and labor cost fields."""
    op.add_column('production_orders', sa.Column('scrap_quantity', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('production_orders', sa.Column('labor_cost', sa.DECIMAL(15, 2), nullable=False, server_default='0.0'))


def downgrade() -> None:
    """Drop fields."""
    op.drop_column('production_orders', 'labor_cost')
    op.drop_column('production_orders', 'scrap_quantity')
