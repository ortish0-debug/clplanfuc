"""add_onec_mapping_table

Revision ID: 160b5dfc954b
Revises: 04d7073a6ce3
Create Date: 2026-06-01 01:28:39.623195

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '160b5dfc954b'
down_revision: Union[str, Sequence[str], None] = '04d7073a6ce3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create onec_mappings table."""
    op.create_table('onec_mappings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('entity_type', sa.String(32), nullable=False),
        sa.Column('internal_id', sa.UUID(), nullable=False),
        sa.Column('onec_guid', sa.String(128), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('onec_guid', name='uq_onec_guid'),
    )


def downgrade() -> None:
    """Drop onec_mappings table."""
    op.drop_table('onec_mappings')
