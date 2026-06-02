"""API: Основные средства (Sprint 18)."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.fixed_assets import FixedAsset
from app.domain.schemas.fixed_assets import FixedAssetCreate, FixedAssetResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.fixed_asset_service import add_fixed_asset, run_monthly_depreciation

router = APIRouter(tags=["Основные средства"])


@router.post(
    "/companies/{company_id}/fixed-assets",
    response_model=FixedAssetResponse,
)
async def create_fixed_asset(
    company_id: UUID,
    req: FixedAssetCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> FixedAssetResponse:
    """Добавить основное средство в учет."""
    asset = await add_fixed_asset(
        db=db,
        company_id=company_id,
        name=req.name,
        inventory_number=req.inventory_number,
        initial_cost=req.initial_cost,
        purchase_date=req.purchase_date,
        lifespan_months=req.lifespan_months,
        account_chart_id=req.account_chart_id,
    )
    await db.commit()
    return FixedAssetResponse.model_validate(asset)


@router.get(
    "/companies/{company_id}/fixed-assets",
    response_model=list[FixedAssetResponse],
)
async def list_fixed_assets(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[FixedAssetResponse]:
    """Получить список всех основных средств компании."""
    result = await db.execute(
        select(FixedAsset).where(FixedAsset.company_id == company_id).order_by(FixedAsset.created_at.desc())
    )
    assets = result.scalars().all()
    return [FixedAssetResponse.model_validate(a) for a in assets]


@router.post(
    "/companies/{company_id}/fixed-assets/depreciate",
)
async def depreciate_fixed_assets(
    company_id: UUID,
    depreciation_date: date = Query(..., description="Дата начисления амортизации (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Начислить амортизацию по всем основным средствам компании."""
    total = await run_monthly_depreciation(db, company_id, depreciation_date)
    await db.commit()
    return {
        "depreciation_date": depreciation_date,
        "total_depreciation": str(total),
        "status": "calculated",
    }
