"""
Доменный слой: модель правил автоматической категоризации транзакций.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base


class MatchField(str, enum.Enum):
    DESCRIPTION      = "description"       # Назначение платежа
    COUNTERPARTY_INN = "counterparty_inn"  # ИНН контрагента
    COUNTERPARTY_NAME= "counterparty_name" # Наименование контрагента


class MatchType(str, enum.Enum):
    CONTAINS = "contains"   # Подстрока (без учёта регистра)
    REGEX    = "regex"      # Регулярное выражение (re.IGNORECASE)
    EXACT    = "exact"      # Точное совпадение (без учёта регистра)


class AutoRule(Base):
    """
    Правило автоматической категоризации банковских операций.

    При разборе выписки движок правил проверяет каждую транзакцию
    против всех активных правил компании (в порядке убывания priority).
    Первое совпавшее правило подставляет suggested_category_id.
    """

    __tablename__ = "auto_rules"

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
        doc="Человекочитаемое название правила"
    )
    field_to_match: Mapped[MatchField] = mapped_column(
        Enum(MatchField, name="match_field_enum"), nullable=False
    )
    match_type: Mapped[MatchType] = mapped_column(
        Enum(MatchType, name="match_type_enum"),
        default=MatchType.CONTAINS,
        nullable=False,
    )
    pattern: Mapped[str] = mapped_column(
        String(512), nullable=False,
        doc="Паттерн для сопоставления (подстрока, regex или точное значение)"
    )
    suggested_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Больший приоритет → правило проверяется раньше
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_auto_rules_company_id",     "company_id"),
        Index("ix_auto_rules_company_active",  "company_id", "is_active"),
        Index("ix_auto_rules_company_priority","company_id", "priority"),
    )

    def __repr__(self) -> str:
        return (
            f"<AutoRule id={self.id} name={self.name!r} "
            f"field={self.field_to_match} pattern={self.pattern!r}>"
        )
