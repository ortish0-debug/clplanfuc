"""Stock movement and COGS processing."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.warehouse import StockLevel
from app.domain.models.warehouse_movements import StockMovement


async def process_stock_movement(db: AsyncSession, movement_data: dict) -> bool:
    """Process stock movement: inbound/outbound/transfer."""
    movement_type = movement_data.get("movement_type")
    warehouse_id = movement_data.get("warehouse_id")
    target_warehouse_id = movement_data.get("target_warehouse_id")
    item_name = movement_data.get("item_name")
    quantity = movement_data.get("quantity", 0)
    unit_cost = Decimal(str(movement_data.get("unit_cost", "0.00")))

    try:
        result = await db.execute(
            select(StockLevel).where(
                StockLevel.warehouse_id == warehouse_id,
                StockLevel.item_name == item_name,
            )
        )
        stock = result.scalar_one_or_none()

        if not stock and movement_type != "inbound":
            return False

        if movement_type == "inbound":
            if stock:
                stock.quantity += quantity
            else:
                stock = StockLevel(
                    warehouse_id=warehouse_id,
                    item_name=item_name,
                    quantity=quantity,
                    minimum_required=0,
                )
                db.add(stock)

        elif movement_type == "outbound":
            stock.quantity = max(0, stock.quantity - quantity)

        elif movement_type == "transfer" and target_warehouse_id:
            stock.quantity -= quantity
            result = await db.execute(
                select(StockLevel).where(
                    StockLevel.warehouse_id == target_warehouse_id,
                    StockLevel.item_name == item_name,
                )
            )
            target_stock = result.scalar_one_or_none()
            if target_stock:
                target_stock.quantity += quantity
            else:
                db.add(
                    StockLevel(
                        warehouse_id=target_warehouse_id,
                        item_name=item_name,
                        quantity=quantity,
                        minimum_required=0,
                    )
                )

        elif movement_type == "audit":
            stock.quantity = quantity

        elif movement_type == "scrap":
            stock.quantity = max(0, stock.quantity - quantity)

        elif movement_type == "reserve_lock":
            stock.reserved = max(0, stock.reserved + quantity)

        elif movement_type == "reserve_unlock":
            stock.reserved = max(0, stock.reserved - quantity)

        db.add(
            StockMovement(
                warehouse_id=warehouse_id,
                target_warehouse_id=target_warehouse_id,
                movement_type=movement_type,
                item_name=item_name,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        )

        await db.flush()
        return True
    except Exception:
        return False
