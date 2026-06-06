"""
Доменный слой: контрагенты и счета-фактуры (дебиторка / кредиторка).

Counterparty        — юрлицо или ИП, с которым работает компания
ContractOrInvoice   — счёт, акт или договор (выставленный или полученный)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import (
    Currency,
    TimestampMixin,
    SoftDeleteMixin,
)


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────


class InvoiceStatus(str, enum.Enum):
    PENDING         = "pending"           # Не оплачен
    PARTIALLY_PAID  = "partially_paid"    # Оплачен частично
    PAID            = "paid"              # Оплачен полностью
    CANCELLED       = "cancelled"         # Отменён


class InvoiceType(str, enum.Enum):
    CUSTOMER_INVOICE = "customer_invoice"  # Выставленный счёт → дебиторка
    SUPPLIER_BILL    = "supplier_bill"     # Полученный счёт   → кредиторка


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTY
# ─────────────────────────────────────────────────────────────────────────────


class Counterparty(Base, TimestampMixin, SoftDeleteMixin):
    """
    Контрагент компании (клиент, поставщик или оба).
    Одна запись может быть и клиентом (is_customer) и поставщиком (is_supplier).
    """

    __tablename__ = "counterparties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    inn: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    kpp: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)

    # Флаги роли контрагента
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active:   Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes:         Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Связи
    invoices: Mapped[list["ContractOrInvoice"]] = relationship(
        back_populates="counterparty", cascade="all, delete-orphan"
    )
    # Документы начислений (Sprint 9)
    accrual_documents: Mapped[list["AccrualDocument"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="AccrualDocument.counterparty_id",
        back_populates="counterparty",
    )

    __table_args__ = (
        Index("ix_counterparties_company_id",   "company_id"),
        Index("ix_counterparties_company_inn",   "company_id", "inn"),
        Index("ix_counterparties_company_active","company_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Counterparty id={self.id} name={self.name!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# CONTRACT / INVOICE
# ─────────────────────────────────────────────────────────────────────────────


class ContractOrInvoice(Base, TimestampMixin, SoftDeleteMixin):
    """
    Счёт-фактура, акт, договор — любой документ,
    порождающий дебиторскую или кредиторскую задолженность.

    customer_invoice → мы выставили счёт клиенту → дебиторка
    supplier_bill    → поставщик выставил нам счёт → кредиторка

    Задолженность = total_amount - paid_amount
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("counterparties.id", ondelete="RESTRICT"),
        nullable=False,
    )

    number:       Mapped[str]           = mapped_column(String(100), nullable=False)
    date:         Mapped[date]          = mapped_column(Date, nullable=False)
    due_date:     Mapped[Optional[date]]= mapped_column(Date, nullable=True)
    description:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    currency:     Mapped[Currency]      = mapped_column(
        Enum(Currency, name="currency_enum"), default=Currency.RUB, nullable=False
    )

    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    paid_amount:  Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False
    )

    # values_callable → SQLAlchemy хранит .value ("pending"), а не .name ("PENDING")
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status_enum",
             values_callable=lambda x: [e.value for e in x]),
        default=InvoiceStatus.PENDING,
        nullable=False,
    )
    invoice_type: Mapped[InvoiceType] = mapped_column(
        Enum(InvoiceType, name="invoice_type_enum",
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # Привязка к проекту (опционально, для проектного учёта)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Связи
    counterparty: Mapped["Counterparty"] = relationship(back_populates="invoices")
    transactions: Mapped[list] = relationship(
        "Transaction",
        foreign_keys="Transaction.invoice_id",
        back_populates="invoice",
    )
    # Ленивая связь к Project (импорт отложен через TYPE_CHECKING)
    project: Mapped[Optional["Project"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="ContractOrInvoice.project_id",
        back_populates="invoices",
    )

    __table_args__ = (
        Index("ix_invoices_company_id",      "company_id"),
        Index("ix_invoices_counterparty_id", "counterparty_id"),
        Index("ix_invoices_company_type",    "company_id", "invoice_type"),
        Index("ix_invoices_company_status",  "company_id", "status"),
        Index("ix_invoices_company_date",    "company_id", "date"),
        Index("ix_invoices_project_id",      "project_id"),
    )

    @property
    def outstanding_amount(self) -> Decimal:
        """Остаток задолженности."""
        return self.total_amount - self.paid_amount

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} type={self.invoice_type} "
            f"status={self.status} amount={self.total_amount}>"
        )
