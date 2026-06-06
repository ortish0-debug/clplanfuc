"""Production execution with COGS."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.production import BOMSpecification, ProductionOrder
from app.services.inventory_service import process_stock_movement


async def execute_production_order(
    db: AsyncSession, order_id: UUID, scrap_qty: int = 0, labor_cost_val: float = 0.0, overhead_cost_val: float = 0.0
) -> bool:
    """Execute production: materials + labor + scrap → GL entries."""
    try:
        result = await db.execute(
            select(ProductionOrder).where(ProductionOrder.id == order_id)
        )
        order = result.scalar_one_or_none()
        if not order:
            return False

        bom_result = await db.execute(
            select(BOMSpecification).where(
                BOMSpecification.company_id == order.company_id,
                BOMSpecification.product_name == order.product_name,
            )
        )
        bom_items = bom_result.scalars().all()

        total_qty = order.quantity_to_produce + scrap_qty
        total_material_cost = Decimal("0.00")
        for bom in bom_items:
            qty = bom.quantity_required * total_qty
            await process_stock_movement(db, {
                "warehouse_id": order.company_id,
                "movement_type": "outbound",
                "item_name": bom.raw_material_name,
                "quantity": qty,
                "unit_cost": 0.0,
            })
            total_material_cost += Decimal(str(qty))

        labor_cost = Decimal(str(labor_cost_val))
        overhead_cost = Decimal(str(overhead_cost_val))
        total_cost = total_material_cost + labor_cost + overhead_cost

        await process_stock_movement(db, {
            "warehouse_id": order.company_id,
            "movement_type": "inbound",
            "item_name": order.product_name,
            "quantity": order.quantity_to_produce,
            "unit_cost": float(total_cost),
        })

        try:
            from app.services.ledger_service import post_double_entry
            await post_double_entry(
                db=db, company_id=order.company_id,
                debit_account="4300", credit_account="2000",
                amount=total_cost,
                description=f"Production: {order.product_name}",
            )
            if labor_cost > 0:
                await post_double_entry(
                    db=db, company_id=order.company_id,
                    debit_account="4300", credit_account="7000",
                    amount=labor_cost,
                    description=f"Labor: {order.product_name}",
                )
            if overhead_cost > 0:
                await post_double_entry(
                    db=db, company_id=order.company_id,
                    debit_account="4300", credit_account="2500",
                    amount=overhead_cost,
                    description=f"Overhead: {order.product_name}",
                )
        except Exception:
            pass

        await db.execute(
            update(ProductionOrder).where(ProductionOrder.id == order_id).values(status="completed")
        )
        await db.flush()
        return True
    except Exception:
        return False
