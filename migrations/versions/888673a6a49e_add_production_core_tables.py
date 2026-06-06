"""add_production_core_tables

Revision ID: 888673a6a49e
Revises: 931ff6f51eec
Create Date: 2026-06-01 00:59:42.590337

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '888673a6a49e'
down_revision: Union[str, Sequence[str], None] = '931ff6f51eec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create BOM and ProductionOrder tables."""
    op.create_table('bom_specifications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('product_name', sa.String(256), nullable=False),
        sa.Column('raw_material_name', sa.String(256), nullable=False),
        sa.Column('quantity_required', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('production_orders',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('product_name', sa.String(256), nullable=False),
        sa.Column('quantity_to_produce', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop production tables."""
    op.drop_table('production_orders')
    op.drop_table('bom_specifications')
