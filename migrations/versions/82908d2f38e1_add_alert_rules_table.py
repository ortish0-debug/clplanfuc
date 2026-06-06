"""add_alert_rules_table

Revision ID: 82908d2f38e1
Revises: 160b5dfc954b
Create Date: 2026-06-01 01:30:33.686499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82908d2f38e1'
down_revision: Union[str, Sequence[str], None] = '160b5dfc954b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create alert_rules table."""
    op.create_table('alert_rules',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('metric_type', sa.String(64), nullable=False),
        sa.Column('threshold_value', sa.DECIMAL(15, 2), nullable=False),
        sa.Column('email_recipient', sa.String(255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop alert_rules table."""
    op.drop_table('alert_rules')
