"""add_planning_table

Revision ID: 04d7073a6ce3
Revises: fb4f0439919c
Create Date: 2026-06-01 01:26:25.022548

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04d7073a6ce3'
down_revision: Union[str, Sequence[str], None] = 'fb4f0439919c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create planned_transactions table."""
    op.create_table('planned_transactions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('transaction_type', sa.String(16), nullable=False),
        sa.Column('plan_date', sa.Date(), nullable=False),
        sa.Column('description', sa.String(512), nullable=True),
        sa.Column('is_executed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop planned_transactions table."""
    op.drop_table('planned_transactions')
