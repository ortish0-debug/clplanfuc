"""add_balance_and_counterparties

Revision ID: e253f1af6ccd
Revises: f097cc44dfb7
Create Date: 2026-05-29 20:14:02.154902

Ручная правка: currency_enum уже существует → используем CREATE TYPE IF NOT EXISTS
и создаём новые типы (invoice_status_enum, invoice_type_enum) через прямой DDL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e253f1af6ccd'
down_revision: Union[str, Sequence[str], None] = 'f097cc44dfb7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Создаём новые ENUM-типы (только если не существуют) ───────────
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE invoice_status_enum AS ENUM ('pending', 'partially_paid', 'paid', 'cancelled');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE invoice_type_enum AS ENUM ('customer_invoice', 'supplier_bill');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))

    # ── 2. Таблица counterparties ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE counterparties (
            id            UUID          NOT NULL PRIMARY KEY,
            company_id    UUID          NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            name          VARCHAR(255)  NOT NULL,
            inn           VARCHAR(12),
            kpp           VARCHAR(9),
            is_customer   BOOLEAN       NOT NULL DEFAULT FALSE,
            is_supplier   BOOLEAN       NOT NULL DEFAULT FALSE,
            is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
            contact_email VARCHAR(255),
            contact_phone VARCHAR(50),
            notes         TEXT,
            created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
            is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE,
            deleted_at    TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ix_counterparties_company_id     ON counterparties (company_id)")
    op.execute("CREATE INDEX ix_counterparties_company_inn    ON counterparties (company_id, inn)")
    op.execute("CREATE INDEX ix_counterparties_company_active ON counterparties (company_id, is_active)")

    # ── 3. Таблица invoices ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE invoices (
            id               UUID                    NOT NULL PRIMARY KEY,
            company_id       UUID                    NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            counterparty_id  UUID                    NOT NULL REFERENCES counterparties(id) ON DELETE RESTRICT,
            number           VARCHAR(100)            NOT NULL,
            date             DATE                    NOT NULL,
            due_date         DATE,
            description      TEXT,
            currency         currency_enum           NOT NULL DEFAULT 'RUB',
            total_amount     NUMERIC(15,2)           NOT NULL,
            paid_amount      NUMERIC(15,2)           NOT NULL DEFAULT 0.00,
            status           invoice_status_enum     NOT NULL DEFAULT 'pending',
            invoice_type     invoice_type_enum       NOT NULL,
            created_at       TIMESTAMPTZ             NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ             NOT NULL DEFAULT now(),
            is_deleted       BOOLEAN                 NOT NULL DEFAULT FALSE,
            deleted_at       TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ix_invoices_company_id       ON invoices (company_id)")
    op.execute("CREATE INDEX ix_invoices_counterparty_id  ON invoices (counterparty_id)")
    op.execute("CREATE INDEX ix_invoices_company_type     ON invoices (company_id, invoice_type)")
    op.execute("CREATE INDEX ix_invoices_company_status   ON invoices (company_id, status)")
    op.execute("CREATE INDEX ix_invoices_company_date     ON invoices (company_id, date)")

    # ── 4. Добавляем FK-колонки в transactions ────────────────────────────
    op.add_column('transactions', sa.Column('counterparty_id', sa.UUID(), nullable=True))
    op.add_column('transactions', sa.Column('invoice_id',      sa.UUID(), nullable=True))

    op.create_foreign_key(
        'fk_transactions_counterparty_id', 'transactions', 'counterparties',
        ['counterparty_id'], ['id'], ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_transactions_invoice_id', 'transactions', 'invoices',
        ['invoice_id'], ['id'], ondelete='SET NULL'
    )

    op.create_index('ix_transactions_counterparty_id', 'transactions', ['counterparty_id'])
    op.create_index('ix_transactions_invoice_id',      'transactions', ['invoice_id'])


def downgrade() -> None:
    op.drop_index('ix_transactions_invoice_id',      table_name='transactions')
    op.drop_index('ix_transactions_counterparty_id', table_name='transactions')
    op.drop_constraint('fk_transactions_invoice_id',      'transactions', type_='foreignkey')
    op.drop_constraint('fk_transactions_counterparty_id', 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'invoice_id')
    op.drop_column('transactions', 'counterparty_id')

    op.drop_index('ix_invoices_company_date',     table_name='invoices')
    op.drop_index('ix_invoices_company_status',   table_name='invoices')
    op.drop_index('ix_invoices_company_type',     table_name='invoices')
    op.drop_index('ix_invoices_counterparty_id',  table_name='invoices')
    op.drop_index('ix_invoices_company_id',       table_name='invoices')
    op.drop_table('invoices')

    op.drop_index('ix_counterparties_company_active', table_name='counterparties')
    op.drop_index('ix_counterparties_company_inn',    table_name='counterparties')
    op.drop_index('ix_counterparties_company_id',     table_name='counterparties')
    op.drop_table('counterparties')

    op.execute("DROP TYPE IF EXISTS invoice_status_enum")
    op.execute("DROP TYPE IF EXISTS invoice_type_enum")
