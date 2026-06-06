"""Warehouse inventory management."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.warehouse import StockLevel, Warehouse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/companies/{company_id}/warehouses",
    tags=["Warehouse"],
)


class StockAlertResponse(BaseModel):
    """Stock alert for critical items."""

    warehouse_name: str
    item_name: str
    quantity: int
    minimum_required: int


@router.get("/alerts", response_model=list[StockAlertResponse], status_code=200)
async def get_warehouse_alerts(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[StockAlertResponse]:
    """Get all stock items below minimum required quantity."""
    result = await db.execute(
        select(Warehouse, StockLevel).join(
            StockLevel, Warehouse.id == StockLevel.warehouse_id
        ).where(
            Warehouse.company_id == company_id,
            StockLevel.quantity < StockLevel.minimum_required,
        )
    )
    rows = result.all()
    return [
        StockAlertResponse(
            warehouse_name=w.name,
            item_name=s.item_name,
            quantity=s.quantity,
            minimum_required=s.minimum_required,
        )
        for w, s in rows
    ]
