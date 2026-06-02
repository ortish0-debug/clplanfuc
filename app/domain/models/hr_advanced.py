"""HR Advanced: leaves, sick days."""
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class EmployeeLeave(Base):
    """Employee vacation/sick leave tracking."""

    __tablename__ = "employee_leaves"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    leave_type: Mapped[str] = mapped_column(String(32))  # vacation, sick, other
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="approved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
