"""1C:Enterprise mapping models."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class OneCMapping(Base):
    __tablename__ = "onec_mappings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=lambda: __import__('uuid').uuid4())
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    internal_id: Mapped[UUID] = mapped_column(nullable=False)
    onec_guid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
