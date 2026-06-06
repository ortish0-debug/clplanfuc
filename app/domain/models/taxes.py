"""Налоговый учёт: НДС, налоговые регистры (Sprint 17)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean, Date, DateTime, Enum as SQLEnum, ForeignKey, Index, Numeric, String, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base


class VatRate(str, Enum):
    """Ставки НДС (для налогового учёта)."""
    VAT_0 = "vat_0"      # 0% (экспорт, льготы)
    VAT_10 = "vat_10"    # 10% (продукты, лекарства)
    VAT_20 = "vat_20"    # 20% (стандартная ставка)
    VAT_NONE = "vat_none"  # Без НДС (услуги, не облагаемые)


class VatRecord(Base):
    """
    Реестр НДС: все операции с налогом.
    - is_input=True → вычет (входящий НДС, к возмещению)
    - is_input=False → уплата (исходящий НДС, к уплате в бюджет)
    """
    __tablename__ = "tax_vat_records"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)

    # Привязка к первичному документу
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)  # 'invoice', 'accrual', etc.
    document_id: Mapped[UUID] = mapped_column(nullable=False)

    # НДС параметры
    vat_rate: Mapped[VatRate] = mapped_column(SQLEnum(VatRate), nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)  # База для НДС
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)    # Сам НДС
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)  # базис + НДС

    # Входящий / Исходящий
    is_input: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Даты
    operation_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relationship
    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        Index("ix_vat_company_date", "company_id", "operation_date"),
        Index("ix_vat_input_output", "company_id", "is_input"),
        Index("ix_vat_doc", "document_type", "document_id"),
    )


# Регистрируем связь в Company (добавляется ниже после импорта Company)
# from app.domain.models.finance import Company
# Company.vat_records = relationship(VatRecord, back_populates="company")
