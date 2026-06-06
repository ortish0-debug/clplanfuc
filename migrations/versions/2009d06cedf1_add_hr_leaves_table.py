"""add_hr_leaves_table

Revision ID: 2009d06cedf1
Revises: 7ea5b5fdc2ff
Create Date: 2026-06-01 00:51:05.444889

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2009d06cedf1'
down_revision: Union[str, Sequence[str], None] = '7ea5b5fdc2ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create employee_leaves table."""
    op.create_table(
        'employee_leaves',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=False),
        sa.Column('leave_type', sa.String(32), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='approved'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_employee_leaves_company_id'), 'employee_leaves', ['company_id'])
    op.create_index(op.f('ix_employee_leaves_employee_id'), 'employee_leaves', ['employee_id'])


def downgrade() -> None:
    """Downgrade schema: drop employee_leaves table."""
    op.drop_index(op.f('ix_employee_leaves_employee_id'), table_name='employee_leaves')
    op.drop_index(op.f('ix_employee_leaves_company_id'), table_name='employee_leaves')
    op.drop_table('employee_leaves')
