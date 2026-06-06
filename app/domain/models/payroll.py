"""
Доменный слой: Сотрудники и Зарплатный конвейер ФОТ (Спринт 10).

Employee           — карточка сотрудника компании.
PayrollCalculation — расчётный листок за один месяц:
                     начислено = оклад + премии;
                     выплачено — обновляется сервисом при регистрации выплат.

Жизненный цикл PayrollCalculation:
  draft   → руководитель ввёл оклад/премии, ещё не утвердил
  accrued → утверждено к выплате (начислено на балансе ФОТ)
  paid    → выплата зафиксирована через ДДС-транзакцию(и)

Одна PayrollCalculation может закрываться несколькими платежами ДДС
(например, аванс + остаток). Поле paid_amount обновляется сервисом
при каждом привязанном платеже.

Уникальность: один расчётный листок на (company, employee, month) —
контролируется UniqueConstraint.
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
    from app.domain.models.finance import Company


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕЧИСЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


class PayrollStatus(str, enum.Enum):
    """
    Статус расчётного листка.

    Жизненный цикл: draft → accrued → paid.
    Возврат draft ← accrued допускается до момента выплаты.
    Возврат paid → accrued — только через корректировочный листок.
    """
    DRAFT   = "draft"    # Черновик: введены данные, не утверждён
    ACCRUED = "accrued"  # Начислено: утверждён, ожидает выплаты
    PAID    = "paid"     # Выплачено: paid_amount >= total_accrued


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE — Карточка сотрудника
# ─────────────────────────────────────────────────────────────────────────────


class Employee(Base, TimestampMixin, SoftDeleteMixin):
    """
    Сотрудник компании.

    Привязывается к Company через company_id.
    Мягкое удаление: is_deleted=True сохраняет историю расчётов.

    Расширение (не в этом спринте): СНИЛС, ИНН сотрудника, реквизиты
    для выгрузки в 1С-Зарплата, ставка НДФЛ, страховые взносы.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(512), nullable=False,
        doc="ФИО сотрудника",
    )
    position: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        doc="Должность / роль (Разработчик, Бухгалтер, Директор...)",
    )
    # Базовый оклад для преднаполнения расчётного листка
    base_salary: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True,
        doc="Оклад по договору (справочное поле, можно переопределить в листке)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False,
        doc="False = уволен (скрыт из активных, история сохранена)",
    )

    # Связи
    company: Mapped["Company"] = relationship()
    payroll_calculations: Mapped[list["PayrollCalculation"]] = relationship(
        back_populates="employee",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_employees_company_id",     "company_id"),
        Index("ix_employees_company_active", "company_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Employee id={self.id} name={self.name!r} pos={self.position!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# PAYROLL CALCULATION — Расчётный листок за месяц
# ─────────────────────────────────────────────────────────────────────────────


class PayrollCalculation(Base, TimestampMixin):
    """
    Расчётный листок сотрудника за конкретный месяц.

    month_date — ВСЕГДА 1-е число месяца (2026-05-01 = май 2026).
    Уникален по (company_id, employee_id, month_date).

    Жизненный цикл сумм:
      accrued_salary  — оклад за месяц (может отличаться от base_salary)
      accrued_bonus   — премия/надбавка
      total_accrued   — итого к выдаче = salary + bonus (хранится явно,
                        т.к. формула может меняться при учёте НДФЛ, вычетов)
      paid_amount     — сколько фактически выплачено через ДДС-транзакции;
                        стартует с 0, обновляется сервисом

    Статус:
      draft   → total_accrued > 0, paid_amount == 0, не утверждён
      accrued → утверждён руководителем, ready-to-pay
      paid    → paid_amount >= total_accrued
    """

    __tablename__ = "payroll_calculations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        doc="RESTRICT: нельзя удалить сотрудника с расчётными листками",
    )

    # 1-е число месяца: 2026-05-01 = расчёт за май 2026
    month_date: Mapped[date] = mapped_column(
        Date, nullable=False,
        doc="Месяц расчёта (всегда 1-е число)",
    )

    # ── Суммы ───────────────────────────────────────────────────────────────
    accrued_salary: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Оклад за месяц (может отличаться от base_salary при неполном месяце)",
    )
    accrued_bonus: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False,
        doc="Премия / надбавка",
    )
    total_accrued: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Итого начислено к выдаче = salary + bonus (без НДФЛ — gross)",
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False,
        doc="Сколько фактически выплачено через ДДС-транзакции",
    )

    # ── Статус ──────────────────────────────────────────────────────────────
    status: Mapped[PayrollStatus] = mapped_column(
        Enum(
            PayrollStatus,
            name="payroll_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=PayrollStatus.DRAFT,
        nullable=False,
    )

    # Комментарий бухгалтера (причина премии, корректировки и т.д.)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Связи
    company:  Mapped["Company"]  = relationship()
    employee: Mapped["Employee"] = relationship()

    __table_args__ = (
        # Один листок на (company, employee, month)
        UniqueConstraint(
            "company_id", "employee_id", "month_date",
            name="uq_payroll_company_employee_month",
        ),
        Index("ix_payroll_company_id",       "company_id"),
        Index("ix_payroll_company_month",     "company_id", "month_date"),
        Index("ix_payroll_employee_id",       "employee_id"),
        Index("ix_payroll_company_status",    "company_id", "status"),
    )

    @property
    def outstanding(self) -> Decimal:
        """Невыплаченный остаток."""
        return max(Decimal("0.00"), self.total_accrued - self.paid_amount)

    @property
    def is_fully_paid(self) -> bool:
        return self.paid_amount >= self.total_accrued

    def __repr__(self) -> str:
        return (
            f"<PayrollCalculation id={self.id} "
            f"month={self.month_date} "
            f"employee_id={self.employee_id} "
            f"total={self.total_accrued} paid={self.paid_amount} "
            f"status={self.status}>"
        )
