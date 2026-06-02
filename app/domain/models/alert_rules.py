"""Alert rules models."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    email_recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TriggeredAlert(Base):
    __tablename__ = "triggered_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        SAUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        SAUUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    actual_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    message: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
