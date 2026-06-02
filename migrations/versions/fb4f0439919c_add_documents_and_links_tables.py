"""add_documents_and_links_tables

Revision ID: fb4f0439919c
Revises: b68a93e6cdb9
Create Date: 2026-06-01 01:21:17.110535

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb4f0439919c'
down_revision: Union[str, Sequence[str], None] = 'b68a93e6cdb9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create documents and links tables."""
    op.create_table('documents',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('doc_type', sa.String(32), nullable=False),
        sa.Column('doc_number', sa.String(128), nullable=False),
        sa.Column('total_amount', sa.DECIMAL(15, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False, server_default='RUB'),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('counterparty_id', sa.UUID(), nullable=True),
        sa.Column('file_url', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['counterparty_id'], ['counterparties.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table('document_transaction_links',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('document_id', sa.UUID(), nullable=False),
        sa.Column('transaction_id', sa.UUID(), nullable=False),
        sa.Column('linked_amount', sa.DECIMAL(15, 2), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop tables."""
    op.drop_table('document_transaction_links')
    op.drop_table('documents')
