"""Planning models."""
from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class PlannedTransaction(Base):
    __tablename__ = "planned_transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=lambda: __import__('uuid').uuid4())
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    amount: Mapped[float] = mapped_column(nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=True)
    is_executed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
