"""API: Налоговый контур (Sprint 17)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.taxes import VatRecord
from app.domain.schemas.taxes import VatRecordResponse, VatSummaryResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CurrentUser
from app.infrastructure.database.session import get_db

router = APIRouter(tags=["Налоги — НДС"])

ZERO = Decimal("0.00")


@router.get(
    "/companies/{company_id}/taxes/vat-records",
    response_model=list[VatRecordResponse],
)
async def get_vat_records(
    company_id: UUID,
    is_input: Optional[bool] = Query(None, description="Фильтр: True=входящий, False=исходящий, None=все"),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[VatRecordResponse]:
    """Получить реестр НДС компании (все или отфильтрованные записи)."""
    query = select(VatRecord).where(VatRecord.company_id == company_id)

    if is_input is not None:
        query = query.where(VatRecord.is_input == is_input)

    query = query.order_by(VatRecord.operation_date.desc())

    result = await db.execute(query)
    records = result.scalars().all()

    return [VatRecordResponse.model_validate(r) for r in records]


@router.get(
    "/companies/{company_id}/taxes/vat-summary",
    response_model=VatSummaryResponse,
)
async def get_vat_summary(
    company_id: UUID,
    from_date: Optional[date] = Query(None, description="Начало периода (YYYY-MM-DD)"),
    to_date: Optional[date] = Query(None, description="Конец периода (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> VatSummaryResponse:
    """Получить сводку НДС: входящий, исходящий, чистый к уплате."""
    query = select(
        VatRecord.is_input,
        func.coalesce(func.sum(VatRecord.vat_amount), ZERO).label("total_vat"),
    ).where(VatRecord.company_id == company_id)

    if from_date:
        query = query.where(VatRecord.operation_date >= from_date)
    if to_date:
        query = query.where(VatRecord.operation_date <= to_date)

    query = query.group_by(VatRecord.is_input)

    result = await db.execute(query)
    rows = result.all()

    input_vat = ZERO
    output_vat = ZERO

    for is_input_val, total_vat in rows:
        if is_input_val:
            input_vat = total_vat or ZERO
        else:
            output_vat = total_vat or ZERO

    net_vat = (output_vat - input_vat).quantize(Decimal("0.01"))

    return VatSummaryResponse(
        total_input_vat=input_vat,
        total_output_vat=output_vat,
        net_vat_to_pay=net_vat,
    )
