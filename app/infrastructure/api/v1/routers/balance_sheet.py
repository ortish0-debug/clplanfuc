"""API: Балансовый отчет (Sprint 16)."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.balance_sheet import BalanceSheetResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.balance_sheet_service import get_balance_sheet

router = APIRouter(tags=["Отчеты — Баланс"])


@router.get(
    "/companies/{company_id}/ledger/balance-sheet",
    response_model=BalanceSheetResponse,
)
async def balance_sheet_report(
    company_id: UUID,
    target_date: Optional[date] = Query(
        None, description="Дата среза баланса. Если не указана — текущая дата"
    ),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> BalanceSheetResponse:
    """Получить управленческий балансовый отчет на дату."""
    if target_date is None:
        target_date = date.today()

    return await get_balance_sheet(db, company_id, target_date)
