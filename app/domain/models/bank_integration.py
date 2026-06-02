"""Bank integration models (Sprint 22)."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class BankIntegration(Base):
    """OAuth2 integration with bank APIs (Tinkoff, Sberbank, etc.)."""

    __tablename__ = "bank_integrations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    bank_name: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<BankIntegration {self.bank_name} company={self.company_id}>"
