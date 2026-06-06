"""
Доменный слой: Документы начислений и связи с платежами (Спринт 9).

Реализует метод начислений (accrual basis accounting):
  - AccrualDocument     — первичный документ (Акт, Реализация, Накладная)
  - TransactionAccrualLink — связь M2M «платёж ↔ документ» с суммой закрытия

Концепция «закрытия» документа:
  Один Акт (AccrualDocument) может быть закрыт несколькими платежами
  (TransactionAccrualLink), или один платёж может закрыть несколько Актов.

  Пример:
    Акт #001 на 300 000 ₽:
      Платёж A (100 000) → linked_amount=100 000
      Платёж B (150 000) → linked_amount=150 000
      Платёж C ( 50 000) → linked_amount= 50 000
    Итого закрыто: 300 000 = amount документа → статус PAID

  Статус вычисляется сервисом (не в модели):
    SUM(links.linked_amount) == 0                    → UNPAID
    0 < SUM(links.linked_amount) < document.amount   → PARTIALLY_PAID
    SUM(links.linked_amount) == document.amount      → PAID
    SUM(links.linked_amount) > document.amount       → OVERPAID (аномалия)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import TimestampMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.domain.models.finance import Company, Category, Transaction
    from app.domain.models.counterparties import Counterparty, ContractOrInvoice
    from app.domain.models.projects import Project


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕЧИСЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


class AccrualDocumentType(str, enum.Enum):
    """
    Тип документа начисления.

    REVENUE — исходящий документ: Акт оказания услуг, Реализация товара,
              УПД. Создаёт дебиторскую задолженность (нам должны).
    EXPENSE — входящий документ: Акт от поставщика, Накладная, УПД от вендора.
              Создаёт кредиторскую задолженность (мы должны).
    """
    REVENUE = "revenue"    # Реализация / Акт выставленный
    EXPENSE = "expense"    # Накладная / Акт полученный


class AccrualStatus(str, enum.Enum):
    """
    Статус оплаченности документа.
    Вычисляется агрегатом SUM(links.linked_amount) / document.amount.
    Хранится денормализованно для быстрой фильтрации без JOIN.
    """
    UNPAID          = "unpaid"           # Ни одного платежа не привязано
    PARTIALLY_PAID  = "partially_paid"   # Оплачено частично
    PAID            = "paid"             # Оплачено полностью
    OVERPAID        = "overpaid"         # Переплата (аномалия — требует ревизии)


# ─────────────────────────────────────────────────────────────────────────────
# ACCRUAL DOCUMENT — Документ начисления
# ─────────────────────────────────────────────────────────────────────────────


class AccrualDocument(Base, TimestampMixin, SoftDeleteMixin):
    """
    Первичный документ, порождающий обязательство по методу начислений.

    Тип REVENUE: Акт оказания услуг, Реализация → появляется дебиторка.
    Тип EXPENSE: Акт от поставщика, Накладная   → появляется кредиторка.

    Связь с платежами — через TransactionAccrualLink (M2M с суммой).
    Связь с контрактом — через contract_id (FK → invoices.id, nullable).

    Статус payment_status хранится денормализованно и обновляется сервисом
    при каждом добавлении/удалении TransactionAccrualLink.
    """

    __tablename__ = "accrual_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Изоляция компании ───────────────────────────────────────────────────
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Участник сделки ─────────────────────────────────────────────────────
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("counterparties.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Контрагент, выставивший или получивший документ",
    )

    # ── Опциональные привязки ───────────────────────────────────────────────
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        doc="Проект (для проектного P&L)",
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        doc="Статья доходов/расходов для P&L-классификации",
    )
    contract_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        doc="Ссылка на ContractOrInvoice (договор/счёт), с которым связан Акт",
    )

    # ── Реквизиты документа ─────────────────────────────────────────────────
    doc_number: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="Номер документа (Акт №42, Реализация 2025-001 и т.д.)",
    )
    doc_date: Mapped[date] = mapped_column(
        Date, nullable=False,
        doc="Дата документа (дата начисления для P&L)",
    )

    # ── Финансовые поля ─────────────────────────────────────────────────────
    amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма документа (без НДС или с НДС — согласно учётной политике)",
    )

    # values_callable → PostgreSQL хранит lowercase
    doc_type: Mapped[AccrualDocumentType] = mapped_column(
        Enum(
            AccrualDocumentType,
            name="accrual_document_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    payment_status: Mapped[AccrualStatus] = mapped_column(
        Enum(
            AccrualStatus,
            name="accrual_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=AccrualStatus.UNPAID,
        nullable=False,
        doc="Денормализованный статус оплаты — обновляется сервисом при изменении links",
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Назначение платежа / описание услуг",
    )

    # ── Холдинговые связи (Sprint 11) ────────────────────────────────────────
    # True = внутригрупповой документ (ВГО): реализация между своими юрлицами.
    # При консолидации такие документы исключаются.
    is_intra_group: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        doc="ВГО-флаг: True = внутрихолдинговый документ, исключается при консолидации",
    )

    # ── НДС (Sprint 10) ──────────────────────────────────────────────────────
    # NULL = без НДС (контрагент-упрощенец, освобождение и т.д.)
    vat_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True,
        doc="Ставка НДС %, NULL = без НДС (0.00 = ставка 0%, 10.00 = 10%, 20.00 = 20%)",
    )
    # Сумма НДС из документа (для книги покупок/продаж)
    vat_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True,
        doc="Сумма НДС в документе, ₽",
    )

    # ── Связи ────────────────────────────────────────────────────────────────
    company:      Mapped["Company"]          = relationship()
    counterparty: Mapped["Counterparty"]     = relationship()
    project:      Mapped[Optional["Project"]] = relationship()
    category:     Mapped[Optional["Category"]] = relationship(foreign_keys=[category_id])
    contract:     Mapped[Optional["ContractOrInvoice"]] = relationship(
        foreign_keys=[contract_id],
    )
    # Связи с платежами (M2M через TransactionAccrualLink)
    payment_links: Mapped[list["TransactionAccrualLink"]] = relationship(
        back_populates="accrual_document",
        cascade="all, delete-orphan",
    )
    # ВГО-связи (Sprint 11): этот документ — источник внутрихолдинговой реализации
    intra_group_as_source: Mapped[list["IntraGroupLink"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="IntraGroupLink.source_accrual_id",
        back_populates="source_accrual",
    )
    # ВГО-связи (Sprint 11): этот документ — получатель внутрихолдинговой закупки
    intra_group_as_dest: Mapped[list["IntraGroupLink"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="IntraGroupLink.destination_accrual_id",
        back_populates="destination_accrual",
    )

    __table_args__ = (
        Index("ix_accrual_docs_company_id",       "company_id"),
        Index("ix_accrual_docs_company_date",      "company_id", "doc_date"),
        Index("ix_accrual_docs_counterparty_id",   "counterparty_id"),
        Index("ix_accrual_docs_project_id",        "project_id"),
        Index("ix_accrual_docs_company_type",      "company_id", "doc_type"),
        Index("ix_accrual_docs_company_status",    "company_id", "payment_status"),
    )

    @property
    def is_revenue(self) -> bool:
        return self.doc_type == AccrualDocumentType.REVENUE

    @property
    def is_fully_paid(self) -> bool:
        return self.payment_status == AccrualStatus.PAID

    def __repr__(self) -> str:
        return (
            f"<AccrualDocument id={self.id} "
            f"doc={self.doc_number!r} type={self.doc_type} "
            f"amount={self.amount} status={self.payment_status}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION ACCRUAL LINK — Связь «платёж ↔ документ»
# ─────────────────────────────────────────────────────────────────────────────


class TransactionAccrualLink(Base):
    """
    Строка распределения платежа по документам начисления (M2M).

    Один платёж (Transaction) может закрывать несколько документов (Accrual).
    Один документ может закрываться несколькими платежами.

    Ограничение:
      UniqueConstraint(transaction_id, accrual_document_id) — один платёж
      не может быть привязан к одному документу более одного раза.
      Для частичных платежей создаётся одна запись с нужной linked_amount.

    Инварианты (контролирует сервис):
      linked_amount > 0
      SUM(linked_amount WHERE accrual_document_id=X) ≤ document.amount
      SUM(linked_amount WHERE transaction_id=Y)       ≤ transaction.amount
    """

    __tablename__ = "transaction_accrual_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    accrual_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accrual_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Сумма из данного платежа, направленная на закрытие данного документа
    linked_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма этого платежа, отнесённая на закрытие данного Акта/Накладной",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Кто создал связь (для аудита)
    linked_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Пользователь, вручную привязавший платёж к документу",
    )

    # Связи
    transaction:      Mapped["Transaction"]      = relationship(back_populates="accrual_links")
    accrual_document: Mapped["AccrualDocument"]  = relationship(back_populates="payment_links")

    __table_args__ = (
        # Один платёж не может быть привязан к одному документу дважды
        UniqueConstraint(
            "transaction_id", "accrual_document_id",
            name="uq_txn_accrual_link",
        ),
        Index("ix_txn_accrual_links_transaction_id",      "transaction_id"),
        Index("ix_txn_accrual_links_accrual_document_id", "accrual_document_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<TransactionAccrualLink "
            f"txn={self.transaction_id} "
            f"doc={self.accrual_document_id} "
            f"amount={self.linked_amount}>"
        )
