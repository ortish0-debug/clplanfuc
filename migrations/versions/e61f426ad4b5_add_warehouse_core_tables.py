"""add_warehouse_core_tables

Revision ID: e61f426ad4b5
Revises: 2009d06cedf1
Create Date: 2026-06-01 00:53:07.404563

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e61f426ad4b5'
down_revision: Union[str, Sequence[str], None] = '2009d06cedf1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create warehouses and stock_levels tables."""
    op.create_table('warehouses',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('location', sa.String(256), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_warehouses_company_id'), 'warehouses', ['company_id'])
    op.create_table('stock_levels',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('warehouse_id', sa.UUID(), nullable=False),
        sa.Column('item_name', sa.String(256), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('minimum_required', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_stock_levels_warehouse_id'), 'stock_levels', ['warehouse_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_stock_levels_warehouse_id'), table_name='stock_levels')
    op.drop_table('stock_levels')
    op.drop_index(op.f('ix_warehouses_company_id'), table_name='warehouses')
    op.drop_table('warehouses')
