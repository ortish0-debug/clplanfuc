"""Stock movements and cost flow."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DECIMAL, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class StockMovement(Base):
    """Warehouse movement: inbound/outbound/transfer."""

    __tablename__ = "stock_movements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    warehouse_id: Mapped[UUID] = mapped_column(ForeignKey("warehouses.id"))
    target_warehouse_id: Mapped[UUID] = mapped_column(
        ForeignKey("warehouses.id"), nullable=True
    )
    movement_type: Mapped[str] = mapped_column(String(16))  # inbound, outbound, transfer
    item_name: Mapped[str] = mapped_column(String(256))
    quantity: Mapped[int]
    unit_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(15, 2), default=Decimal("0.00")
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
