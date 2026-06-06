"""API: Налоговые декларации и отчёты (Sprint 20)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.tax_reports import VatDeclarationResponse, ProfitTaxReportResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.tax_report_service import generate_vat_declaration, generate_profit_tax_report

router = APIRouter(tags=["Налоговые отчёты"])


@router.get(
    "/companies/{company_id}/tax-reports/vat",
    response_model=VatDeclarationResponse,
)
async def get_vat_declaration(
    company_id: UUID,
    year: int = Query(..., ge=2020, le=2099),
    quarter: int = Query(..., ge=1, le=4),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> VatDeclarationResponse:
    """Сгенерировать декларацию по НДС за квартал."""
    result = await generate_vat_declaration(db, company_id, year, quarter)
    return VatDeclarationResponse(**result)


@router.get(
    "/companies/{company_id}/tax-reports/profit",
    response_model=ProfitTaxReportResponse,
)
async def get_profit_tax_report(
    company_id: UUID,
    year: int = Query(..., ge=2020, le=2099),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> ProfitTaxReportResponse:
    """Сгенерировать налоговый отчёт по прибыли за год."""
    result = await generate_profit_tax_report(db, company_id, year)
    return ProfitTaxReportResponse(**result)
