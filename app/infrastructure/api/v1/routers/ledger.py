"""API: План счетов и Журнал проводок (Спринт 14)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.models.ledger import AccountChart, JournalEntry
from app.domain.schemas.ledger import AccountChartResponse, JournalEntryResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.ledger_service import initialize_default_chart

router = APIRouter(tags=["План счетов"])


@router.post("/companies/{company_id}/ledger/accounts/init")
async def init_chart(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Инициализировать дефолтный план счетов."""
    await initialize_default_chart(db, company_id)
    await db.commit()
    return {"status": "initialized"}


@router.get("/companies/{company_id}/ledger/accounts", response_model=list[AccountChartResponse])
async def get_accounts(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[AccountChartResponse]:
    """Получить план счетов компании."""
    result = await db.execute(
        select(AccountChart)
        .where(AccountChart.company_id == company_id)
        .order_by(AccountChart.code)
    )
    accounts = result.scalars().all()
    return [AccountChartResponse.model_validate(acc) for acc in accounts]


@router.get("/companies/{company_id}/ledger/entries", response_model=list[JournalEntryResponse])
async def get_journal_entries(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[JournalEntryResponse]:
    """Получить журнал проводок компании."""
    result = await db.execute(
        select(JournalEntry)
        .where(JournalEntry.company_id == company_id)
        .options(selectinload(JournalEntry.lines))
        .order_by(JournalEntry.operation_date.desc())
    )
    entries = result.scalars().all()
    return [JournalEntryResponse.model_validate(entry) for entry in entries]
