"""Multi-warehouse inventory management."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class Warehouse(Base):
    """Physical warehouse."""

    __tablename__ = "warehouses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(256), nullable=True)


class StockLevel(Base):
    """Inventory position per warehouse."""

    __tablename__ = "stock_levels"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    warehouse_id: Mapped[UUID] = mapped_column(ForeignKey("warehouses.id"))
    item_name: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    minimum_required: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )
