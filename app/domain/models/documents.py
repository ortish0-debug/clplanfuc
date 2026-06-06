"""Primary documents: Invoices, Acts, Contracts."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DECIMAL, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class Document(Base):
    """Primary document (invoice, act, contract)."""
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    doc_type: Mapped[str] = mapped_column(String(32))
    doc_number: Mapped[str] = mapped_column(String(128))
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    counterparty_id: Mapped[UUID] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    file_url: Mapped[str] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class DocumentTransactionLink(Base):
    """Link document to transaction payments."""
    __tablename__ = "document_transaction_links"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("transactions.id"))
    linked_amount: Mapped[Decimal] = mapped_column(DECIMAL(15, 2))
