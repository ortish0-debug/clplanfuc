"""Production API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.production import BOMSpecification, ProductionOrder
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.production_service import execute_production_order
from sqlalchemy import select

router = APIRouter(
    prefix="/companies/{company_id}/production",
    tags=["Production"],
)


class BOMRequest(BaseModel):
    product_name: str
    raw_material_name: str
    quantity_required: int


class OrderRequest(BaseModel):
    product_name: str
    quantity_to_produce: int


class ExecuteOrderRequest(BaseModel):
    scrap_qty: int = 0
    labor_cost: float = 0.0
    overhead_cost: float = 0.0


@router.post("/specs", status_code=201)
async def create_bom(
    company_id: UUID,
    request: BOMRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create BOM specification."""
    bom = BOMSpecification(
        company_id=company_id,
        product_name=request.product_name,
        raw_material_name=request.raw_material_name,
        quantity_required=request.quantity_required,
    )
    db.add(bom)
    await db.commit()
    return {"status": "success", "bom_id": str(bom.id)}


@router.post("/orders", status_code=201)
async def create_order(
    company_id: UUID,
    request: OrderRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create production order."""
    order = ProductionOrder(
        company_id=company_id,
        product_name=request.product_name,
        quantity_to_produce=request.quantity_to_produce,
        status="pending",
    )
    db.add(order)
    await db.commit()
    return {"status": "success", "order_id": str(order.id)}


@router.post("/orders/{order_id}/execute", status_code=200)
async def execute_order(
    company_id: UUID,
    order_id: UUID,
    request: ExecuteOrderRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Execute production order: consume materials + labor + overhead."""
    success = await execute_production_order(
        db, order_id, request.scrap_qty, request.labor_cost, request.overhead_cost
    )
    if success:
        await db.commit()
        return {"status": "success", "message": "Production order executed"}
    return {"status": "error", "message": "Failed to execute order"}


@router.get("/orders/{order_id}/variance", status_code=200)
async def get_variance(
    company_id: UUID,
    order_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Calculate plan vs actual cost variance."""
    result = await db.execute(
        select(ProductionOrder).where(ProductionOrder.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return {"status": "error"}

    planned_cost = 0.0
    actual_cost = float(order.labor_cost) if order.labor_cost else 0.0
    variance = planned_cost - actual_cost
    variance_pct = (variance / planned_cost * 100) if planned_cost > 0 else 0.0

    return {
        "planned_cost": planned_cost,
        "actual_cost": actual_cost,
        "variance": variance,
        "variance_pct": variance_pct,
    }
