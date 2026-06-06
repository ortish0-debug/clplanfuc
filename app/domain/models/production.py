"""Production planning and execution."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DECIMAL, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class BOMSpecification(Base):
    """Bill of Materials: raw materials needed for product."""

    __tablename__ = "bom_specifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    product_name: Mapped[str] = mapped_column(String(256))
    raw_material_name: Mapped[str] = mapped_column(String(256))
    quantity_required: Mapped[int] = mapped_column(Integer)


class ProductionOrder(Base):
    """Manufacturing order."""

    __tablename__ = "production_orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    product_name: Mapped[str] = mapped_column(String(256))
    quantity_to_produce: Mapped[int] = mapped_column(Integer)
    scrap_quantity: Mapped[int] = mapped_column(Integer, default=0)
    labor_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), default=Decimal("0.00"))
    overhead_cost: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
