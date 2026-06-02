"""Inventory movements API."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.inventory_service import process_stock_movement

router = APIRouter(
    prefix="/companies/{company_id}/inventory",
    tags=["Inventory"],
)

VALID_MOVEMENT_TYPES = [
    "inbound", "outbound", "transfer", "audit", "scrap", "reserve_lock", "reserve_unlock"
]


class StockMovementRequest(BaseModel):
    """Stock movement request."""

    warehouse_id: UUID
    target_warehouse_id: UUID = None
    movement_type: Literal[
        "inbound", "outbound", "transfer", "audit", "scrap", "reserve_lock", "reserve_unlock"
    ]
    item_name: str
    quantity: int
    unit_cost: float = 0.0


@router.post("/move", status_code=200)
async def move_stock(
    company_id: UUID,
    request: StockMovementRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Process stock movement (inbound/outbound/transfer)."""
    success = await process_stock_movement(db, request.model_dump())
    if success:
        await db.commit()
        return {"status": "success", "message": "Stock movement recorded"}
    return {"status": "error", "message": "Failed to process movement"}
