"""Основные средства (ОС) и амортизация (Sprint 18)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Date, DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, Numeric, String, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base


class FixedAssetStatus(str, Enum):
    """Статус основного средства."""
    ACTIVE = "active"              # Введено в эксплуатацию
    DEPRECIATED = "depreciated"    # Полностью амортизировано
    WRITTEN_OFF = "written_off"    # Списано/выбыло


class FixedAsset(Base):
    """
    Основное средство (ОС): оборудование, здания, транспорт и т.д.
    Отслеживает первоначальную стоимость и накопленную амортизацию.
    """
    __tablename__ = "fixed_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)

    # Описание ОС
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # Наименование
    inventory_number: Mapped[str] = mapped_column(String(64), nullable=False)  # Уникальный инвентарный номер

    # Стоимость и дата
    initial_cost: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)  # Первоначальная стоимость
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)  # Дата покупки

    # Амортизация
    lifespan_months: Mapped[int] = mapped_column(Integer, nullable=False)  # Срок полезного использования (месяцы)
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False
    )  # Накопленная амортизация

    # Статус
    status: Mapped[FixedAssetStatus] = mapped_column(
        SQLEnum(FixedAssetStatus), default=FixedAssetStatus.ACTIVE, nullable=False
    )

    # Привязка к счету учета (опционально)
    account_chart_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts_chart.id"), nullable=True
    )

    # Даты
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    company: Mapped[Company] = relationship("Company")

    __table_args__ = (
        Index("ix_fixed_assets_company_status", "company_id", "status"),
    )
