"""add_overhead_cost_to_orders

Revision ID: b68a93e6cdb9
Revises: bf39510db1f2
Create Date: 2026-06-01 01:06:19.795385

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b68a93e6cdb9'
down_revision: Union[str, Sequence[str], None] = 'bf39510db1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add overhead cost field."""
    op.add_column('production_orders', sa.Column('overhead_cost', sa.DECIMAL(15, 2), nullable=False, server_default='0.0'))


def downgrade() -> None:
    """Drop overhead cost field."""
    op.drop_column('production_orders', 'overhead_cost')
