"""Alert rules models."""
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=lambda: __import__('uuid').uuid4())
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    email_recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
