"""CRM Deals — воронка продаж (Kanban)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.crm_deals import Deal, DealStatus
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db

router = APIRouter(tags=["CRM Сделки"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class DealCreate(BaseModel):
    title: str
    counterparty_name: Optional[str] = None
    counterparty_id: Optional[UUID] = None
    amount: Optional[Decimal] = None
    currency: str = "RUB"
    status: DealStatus = DealStatus.LEAD
    probability: Optional[int] = None
    expected_close_date: Optional[date] = None
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    sort_order: int = 0
    tags: list = []


class DealUpdate(BaseModel):
    title: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_id: Optional[UUID] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    status: Optional[DealStatus] = None
    probability: Optional[int] = None
    expected_close_date: Optional[date] = None
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    sort_order: Optional[int] = None
    tags: Optional[list] = None


class DealMoveRequest(BaseModel):
    status: DealStatus
    sort_order: int = 0


def _deal_to_dict(deal: Deal) -> dict:
    return {
        "id": str(deal.id),
        "company_id": str(deal.company_id),
        "title": deal.title,
        "counterparty_name": deal.counterparty_name,
        "counterparty_id": str(deal.counterparty_id) if deal.counterparty_id else None,
        "amount": float(deal.amount) if deal.amount is not None else None,
        "currency": deal.currency,
        "status": deal.status.value,
        "probability": deal.probability,
        "expected_close_date": deal.expected_close_date.isoformat() if deal.expected_close_date else None,
        "description": deal.description,
        "assigned_to": deal.assigned_to,
        "sort_order": deal.sort_order,
        "tags": deal.tags or [],
        "created_at": deal.created_at.isoformat(),
        "updated_at": deal.updated_at.isoformat(),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/companies/{company_id}/crm/deals")
async def list_deals(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Deal)
        .where(Deal.company_id == company_id)
        .order_by(Deal.sort_order, Deal.created_at)
    )
    deals = result.scalars().all()
    return {"deals": [_deal_to_dict(d) for d in deals]}


@router.post("/companies/{company_id}/crm/deals", status_code=status.HTTP_201_CREATED)
async def create_deal(
    company_id: UUID,
    payload: DealCreate,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    deal = Deal(
        company_id=company_id,
        **payload.model_dump(exclude_unset=False),
    )
    db.add(deal)
    await db.commit()
    await db.refresh(deal)
    return _deal_to_dict(deal)


@router.get("/companies/{company_id}/crm/deals/stats")
async def deals_stats(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Deal.status, func.count(Deal.id), func.sum(Deal.amount))
        .where(Deal.company_id == company_id)
        .group_by(Deal.status)
    )
    rows = result.all()
    stats = {}
    for row in rows:
        stats[row[0].value] = {
            "count": row[1],
            "total_amount": float(row[2]) if row[2] else 0.0,
        }
    return {"stats": stats}


@router.get("/companies/{company_id}/crm/deals/{deal_id}")
async def get_deal(
    company_id: UUID,
    deal_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Deal).where(and_(Deal.id == deal_id, Deal.company_id == company_id))
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")
    return _deal_to_dict(deal)


@router.patch("/companies/{company_id}/crm/deals/{deal_id}")
async def update_deal(
    company_id: UUID,
    deal_id: UUID,
    payload: DealUpdate,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Deal).where(and_(Deal.id == deal_id, Deal.company_id == company_id))
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(deal, field, value)

    await db.commit()
    await db.refresh(deal)
    return _deal_to_dict(deal)


@router.patch("/companies/{company_id}/crm/deals/{deal_id}/move")
async def move_deal(
    company_id: UUID,
    deal_id: UUID,
    payload: DealMoveRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Deal).where(and_(Deal.id == deal_id, Deal.company_id == company_id))
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")

    deal.status = payload.status
    deal.sort_order = payload.sort_order

    await db.commit()
    await db.refresh(deal)
    return _deal_to_dict(deal)


@router.delete("/companies/{company_id}/crm/deals/{deal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deal(
    company_id: UUID,
    deal_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Deal).where(and_(Deal.id == deal_id, Deal.company_id == company_id))
    )
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")

    await db.delete(deal)
    await db.commit()
