"""Currency exchange rates model (Sprint 21)."""
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class CurrencyRate(Base):
    """Daily currency exchange rates to RUB from CBR (Central Bank of Russia)."""

    __tablename__ = "currency_rates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    currency_code: Mapped[str] = mapped_column(default="USD")
    rate: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("currency_code", "rate_date", name="ux_currency_code_date"),
        Index("ix_currency_rates_date", "rate_date"),
        Index("ix_currency_rates_code", "currency_code"),
    )

    def __repr__(self) -> str:
        return f"<CurrencyRate {self.currency_code}={self.rate} @{self.rate_date}>"
