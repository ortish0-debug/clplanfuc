"""
Доменный слой: Имущество и Кредиты (Спринт 7).

Asset                — карточка основного средства с амортизацией
Loan                 — кредит/заём с аннуитетным или дифференцированным графиком
LoanPaymentSchedule  — плановый график платежей по кредиту
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
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import TimestampMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.domain.models.finance import Transaction, Account
    from app.domain.models.counterparties import Counterparty


# ─────────────────────────────────────────────────────────────────────────────
# ENUM
# ─────────────────────────────────────────────────────────────────────────────


class LoanType(str, enum.Enum):
    ANNUITY       = "annuity"        # Аннуитетный: равные ежемесячные платежи
    DIFFERENTIATED = "differentiated"  # Дифференцированный: убывающие платежи


# ─────────────────────────────────────────────────────────────────────────────
# ASSET — Основное средство
# ─────────────────────────────────────────────────────────────────────────────


class Asset(Base, TimestampMixin, SoftDeleteMixin):
    """
    Карточка основного средства компании.

    Линейная амортизация:
      monthly_amortization = purchase_cost / amortization_months
      residual_value        = purchase_cost - accumulated_amortization

    accumulated_amortization обновляется ежемесячно сервисом амортизации.
    """

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )

    name:        Mapped[str]           = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Стоимость и амортизация
    purchase_cost: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Первоначальная стоимость приобретения",
    )
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    amortization_months: Mapped[int] = mapped_column(
        Integer, nullable=False,
        doc="Срок полезного использования в месяцах (например, 36 = 3 года)",
    )
    accumulated_amortization: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False,
        doc="Накопленная амортизация (сумма начислений за все периоды)",
    )

    # Счёт, через который было оплачено приобретение (опционально)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Связи
    account:      Mapped[Optional["Account"]]      = relationship(foreign_keys=[account_id])
    transactions: Mapped[list["Transaction"]]       = relationship(
        foreign_keys="Transaction.asset_id",
        back_populates="asset",
    )

    __table_args__ = (
        Index("ix_assets_company_id",     "company_id"),
        Index("ix_assets_company_active", "company_id", "is_active"),
        Index("ix_assets_purchase_date",  "company_id", "purchase_date"),
    )

    @property
    def monthly_amortization(self) -> Decimal:
        """Ежемесячная сумма амортизации (линейный метод)."""
        if self.amortization_months <= 0:
            return Decimal("0.00")
        return (self.purchase_cost / Decimal(self.amortization_months)).quantize(
            Decimal("0.01")
        )

    @property
    def residual_value(self) -> Decimal:
        """Остаточная стоимость = первоначальная − накопленная амортизация."""
        return max(Decimal("0.00"), self.purchase_cost - self.accumulated_amortization)

    @property
    def is_fully_amortized(self) -> bool:
        """True если остаточная стоимость равна нулю."""
        return self.residual_value == Decimal("0.00")

    def __repr__(self) -> str:
        return (
            f"<Asset id={self.id} name={self.name!r} "
            f"cost={self.purchase_cost} residual={self.residual_value}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LOAN — Кредит / Заём
# ─────────────────────────────────────────────────────────────────────────────


class Loan(Base, TimestampMixin, SoftDeleteMixin):
    """
    Кредит или заём компании.

    Поддерживает два метода расчёта:
      ANNUITY        — одинаковый ежемесячный платёж (PMT-формула)
      DIFFERENTIATED — убывающий платёж: тело кредита = total / term,
                       проценты начисляются на остаток долга.

    remaining_principal обновляется при регистрации каждого платежа.
    """

    __tablename__ = "loans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        doc="Название кредита (например, 'Кредит Альфа-Банк 2025')",
    )

    # Кредитор (опционально — может быть банк или физлицо без карточки)
    counterparty_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("counterparties.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Счёт, на который поступили/с которого списываются средства
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Параметры кредита
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма кредита (тело)",
    )
    interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False,
        doc="Годовая процентная ставка, % (например 15.5000 = 15.5%)",
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    term_months: Mapped[int] = mapped_column(
        Integer, nullable=False,
        doc="Срок кредита в месяцах",
    )

    # values_callable → PostgreSQL хранит lowercase "annuity"/"differentiated"
    loan_type: Mapped[LoanType] = mapped_column(
        Enum(
            LoanType,
            name="loan_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # Текущий остаток основного долга (обновляется при каждом платеже)
    remaining_principal: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Остаток основного долга на текущий момент",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Связи
    counterparty: Mapped[Optional["Counterparty"]] = relationship(
        foreign_keys=[counterparty_id]
    )
    account: Mapped[Optional["Account"]] = relationship(
        foreign_keys=[account_id]
    )
    schedule: Mapped[list["LoanPaymentSchedule"]] = relationship(
        back_populates="loan",
        cascade="all, delete-orphan",
        order_by="LoanPaymentSchedule.payment_date",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        foreign_keys="Transaction.loan_id",
        back_populates="loan",
    )

    __table_args__ = (
        Index("ix_loans_company_id",     "company_id"),
        Index("ix_loans_company_active", "company_id", "is_active"),
        Index("ix_loans_counterparty",   "counterparty_id"),
    )

    @property
    def monthly_rate(self) -> Decimal:
        """Месячная процентная ставка (десятичная дробь)."""
        return (self.interest_rate / Decimal("100") / Decimal("12")).quantize(
            Decimal("0.000001")
        )

    @property
    def paid_principal(self) -> Decimal:
        """Выплаченное тело кредита = total_amount − remaining_principal."""
        return max(Decimal("0.00"), self.total_amount - self.remaining_principal)

    def __repr__(self) -> str:
        return (
            f"<Loan id={self.id} name={self.name!r} "
            f"amount={self.total_amount} rate={self.interest_rate}% "
            f"type={self.loan_type}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LOAN PAYMENT SCHEDULE — График платежей
# ─────────────────────────────────────────────────────────────────────────────


class LoanPaymentSchedule(Base):
    """
    Одна строка графика погашения кредита.

    Генерируется при создании Loan сервисом loan_calculator.
    При регистрации фактического платежа:
      is_paid = True, transaction_id → соответствующая Transaction.
    """

    __tablename__ = "loan_payment_schedule"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loans.id", ondelete="CASCADE"),
        nullable=False,
    )

    payment_date:     Mapped[date]    = mapped_column(Date, nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма погашения тела кредита в этот период",
    )
    interest_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма процентов в этот период",
    )
    total_payment: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="principal_amount + interest_amount",
    )

    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Ссылка на фактическую транзакцию оплаты (заполняется при регистрации платежа)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Связи
    loan: Mapped["Loan"] = relationship(back_populates="schedule")

    __table_args__ = (
        Index("ix_loan_schedule_loan_id",   "loan_id"),
        Index("ix_loan_schedule_date",      "loan_id", "payment_date"),
        Index("ix_loan_schedule_unpaid",    "loan_id", "is_paid"),
    )

    def __repr__(self) -> str:
        return (
            f"<LoanPaymentSchedule loan={self.loan_id} "
            f"date={self.payment_date} total={self.total_payment} "
            f"paid={self.is_paid}>"
        )
