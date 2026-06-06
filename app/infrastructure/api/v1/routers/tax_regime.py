"""API: Управление налоговым режимом компании."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.auth import UpdateTaxRegimeRequest
from app.infrastructure.api.v1.dependencies.auth import CanSettings, CanViewDashboard, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.tax_regime_service import (
    calculate_tax,
    get_all_regimes,
    get_company_tax_regime,
    update_company_tax_regime,
)

router = APIRouter(tags=["Налоговый режим"])


@router.get("/tax-regimes", summary="Список всех налоговых режимов РФ")
async def list_tax_regimes() -> list[dict]:
    """Вернуть все поддерживаемые налоговые режимы с описанием и ставками."""
    return get_all_regimes()


@router.get(
    "/companies/{company_id}/tax-regime",
    summary="Текущий налоговый режим компании",
)
async def get_tax_regime(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Получить текущий налоговый режим компании."""
    return await get_company_tax_regime(db, company_id)


@router.patch(
    "/companies/{company_id}/tax-regime",
    summary="Сменить налоговый режим компании",
)
async def change_tax_regime(
    company_id: UUID,
    body: UpdateTaxRegimeRequest,
    current_user: CurrentUser = Depends(CanSettings),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Сменить налоговый режим компании.
    Доступно только владельцу и администратору.
    """
    return await update_company_tax_regime(db, company_id, body.tax_regime)


@router.get(
    "/companies/{company_id}/tax-regime/calculate",
    summary="Рассчитать налог по текущему режиму",
)
async def calculate_company_tax(
    company_id: UUID,
    income: Decimal = Query(..., description="Сумма доходов"),
    expenses: Decimal = Query(Decimal("0"), description="Сумма расходов"),
    from_individuals: bool = Query(True, description="Доход от физлиц (для НПД)"),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Рассчитать сумму налога для компании на основе её налогового режима.
    """
    regime_info = await get_company_tax_regime(db, company_id)
    regime = regime_info.get("tax_regime", "usn_income")
    result = calculate_tax(regime, income, expenses, from_individuals)
    result["regime_name"] = regime_info.get("name", "")
    return result
