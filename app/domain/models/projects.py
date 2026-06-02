"""
Доменный слой: Проекты, Бюджеты, Заявки на оплату (Спринт 6).

Структура:
  Project         — центр группировки операций и бюджетов
  Budget          — плановая сумма расхода/дохода по категории × месяц × проект
  PaymentRequest  — заявка на оплату (согласование перед созданием Transaction)
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
    Integer,
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
    from app.domain.models.finance import Category, Transaction
    from app.domain.models.counterparties import ContractOrInvoice
    from app.domain.models.saas import User


# ─────────────────────────────────────────────────────────────────────────────
# ENUM
# ─────────────────────────────────────────────────────────────────────────────


class PaymentRequestStatus(str, enum.Enum):
    PENDING  = "pending"   # Ожидает согласования
    APPROVED = "approved"  # Согласована
    REJECTED = "rejected"  # Отклонена
    PAID     = "paid"      # Оплачена (создана соответствующая Transaction)


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────


class Project(Base, TimestampMixin, SoftDeleteMixin):
    """
    Проект — группировочная сущность для операций и бюджетов.
    Операции, счета и заявки могут быть привязаны к конкретному проекту.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # #RRGGBB
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Связи
    budgets: Mapped[list["Budget"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    payment_requests: Mapped[list["PaymentRequest"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        foreign_keys="Transaction.project_id",
        back_populates="project",
    )
    invoices: Mapped[list["ContractOrInvoice"]] = relationship(
        foreign_keys="ContractOrInvoice.project_id",
        back_populates="project",
    )
    # Документы начислений (Sprint 9)
    accrual_documents: Mapped[list["AccrualDocument"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="AccrualDocument.project_id",
        back_populates="project",
    )

    __table_args__ = (
        Index("ix_projects_company_id",     "company_id"),
        Index("ix_projects_company_active", "company_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name!r} company_id={self.company_id}>"


# ─────────────────────────────────────────────────────────────────────────────
# BUDGET
# ─────────────────────────────────────────────────────────────────────────────


class Budget(Base, TimestampMixin):
    """
    Плановый бюджет по категории на конкретный месяц.

    Уникальность: (company_id, category_id, project_id, year, month).
    Примечание: PostgreSQL считает NULL-значения различными в уникальных
    ограничениях, поэтому для одной категории могут сосуществовать
    бюджеты с project_id=NULL и с конкретным project_id.
    """

    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    year:  Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–12

    plan_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False
    )

    # Связи
    project:  Mapped[Optional["Project"]]  = relationship(back_populates="budgets")
    category: Mapped["Category"]           = relationship()

    __table_args__ = (
        # Один план-бюджет на связку категория × (проект?) × месяц
        UniqueConstraint(
            "company_id", "category_id", "project_id", "year", "month",
            name="uq_budget_category_project_month",
        ),
        Index("ix_budgets_company_id",            "company_id"),
        Index("ix_budgets_company_year_month",     "company_id", "year", "month"),
        Index("ix_budgets_category_id",            "category_id"),
        Index("ix_budgets_project_id",             "project_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Budget id={self.id} category_id={self.category_id} "
            f"{self.year}-{self.month:02d} plan={self.plan_amount}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENT REQUEST
# ─────────────────────────────────────────────────────────────────────────────


class PaymentRequest(Base, TimestampMixin):
    """
    Заявка на оплату — документ согласования перед созданием Transaction.

    Жизненный цикл:
      PENDING → (approve) → APPROVED → (pay / create transaction) → PAID
      PENDING → (reject)  → REJECTED
    """

    __tablename__ = "payment_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    applicant_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Пользователь, одобривший/отклонивший заявку
    reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)

    # values_callable → PostgreSQL хранит lowercase "pending", не "PENDING"
    status: Mapped[PaymentRequestStatus] = mapped_column(
        Enum(
            PaymentRequestStatus,
            name="payment_request_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=PaymentRequestStatus.PENDING,
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Причина отклонения (заполняется при REJECTED)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Ссылка на созданную транзакцию после оплаты
    paid_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Связи
    project:   Mapped[Optional["Project"]]  = relationship(back_populates="payment_requests")
    category:  Mapped[Optional["Category"]] = relationship(
        foreign_keys=[category_id]
    )
    applicant: Mapped["User"] = relationship(
        foreign_keys=[applicant_user_id]
    )
    reviewer:  Mapped[Optional["User"]] = relationship(
        foreign_keys=[reviewer_user_id]
    )

    __table_args__ = (
        Index("ix_payment_requests_company_id",     "company_id"),
        Index("ix_payment_requests_company_status", "company_id", "status"),
        Index("ix_payment_requests_applicant",      "applicant_user_id"),
        Index("ix_payment_requests_project_id",     "project_id"),
        Index("ix_payment_requests_planned_date",   "company_id", "planned_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<PaymentRequest id={self.id} amount={self.amount} "
            f"status={self.status} date={self.planned_date}>"
        )
