"""Reports API: P&L, Cash Flow."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.report_service import generate_pl_report, generate_cash_flow_report, generate_balance_sheet, generate_financial_ratios

router = APIRouter(
    prefix="/companies/{company_id}/reports",
    tags=["Reports"],
)


@router.get("/pl", status_code=200)
async def get_pl_report(
    company_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get P&L report."""
    return await generate_pl_report(db, company_id, start_date, end_date)


@router.get("/cashflow", status_code=200)
async def get_cashflow_report(
    company_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get Cash Flow report."""
    return await generate_cash_flow_report(db, company_id, start_date, end_date)


@router.get("/balance", status_code=200)
async def get_balance_sheet(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get Balance Sheet report."""
    return await generate_balance_sheet(db, company_id)


@router.get("/ratios", status_code=200)
async def get_financial_ratios(
    company_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get Financial Ratios: ROI, ROE, EBITDA Margin, Health Score."""
    return await generate_financial_ratios(db, company_id, start_date, end_date)
