"""add_stock_movements_table

Revision ID: 0e51cb14275b
Revises: e61f426ad4b5
Create Date: 2026-06-01 00:55:17.065453

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e51cb14275b'
down_revision: Union[str, Sequence[str], None] = 'e61f426ad4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create stock_movements table."""
    op.create_table('stock_movements',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('warehouse_id', sa.UUID(), nullable=False),
        sa.Column('target_warehouse_id', sa.UUID(), nullable=True),
        sa.Column('movement_type', sa.String(16), nullable=False),
        sa.Column('item_name', sa.String(256), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_cost', sa.DECIMAL(15, 2), nullable=False, server_default='0.00'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id']),
        sa.ForeignKeyConstraint(['target_warehouse_id'], ['warehouses.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_stock_movements_warehouse_id'), 'stock_movements', ['warehouse_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_stock_movements_warehouse_id'), table_name='stock_movements')
    op.drop_table('stock_movements')
