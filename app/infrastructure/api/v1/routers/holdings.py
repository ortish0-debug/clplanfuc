"""
FastAPI роутер: Холдинг и консолидация ВГО (Спринт 11).

Эндпоинты:
  POST /companies/holdings/links/transactions  — связать два платежа как ВГО
  POST /companies/holdings/links/accruals      — связать две первички как ВГО
  GET  /companies/holdings/reports/dds         — консолидированный ДДС без ВГО
  GET  /companies/holdings/reports/pnl         — консолидированный P&L без ВГО

Архитектура:
  Холдинговые роутеры — надкомпанийные: они не привязаны к {company_id} в пути,
  а принимают списки company_ids. RBAC проверяется через суперпользователя
  (is_superuser) или через is_superuser + принадлежность к любой из компаний.
  Для MVP: требуем is_superuser или CanViewReports/CanWriteFinance хотя бы
  для одной компании (через CurrentUser).
"""
from __future__ import annotations

from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.holdings import (
    ConsolidatedDDSResponse,
    ConsolidatedPnLResponse,
    IntraGroupAccrualLinkRequest,
    IntraGroupLinkResponse,
    IntraGroupTransactionLinkRequest,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CurrentUser,
    get_current_user,
)
from app.infrastructure.database.session import get_db
from app.services.holding_service import (
    create_intra_group_accrual_link,
    create_intra_group_transaction_link,
    get_consolidated_dds_totals,
    get_consolidated_pnl_totals,
)

router = APIRouter(tags=["Холдинг — консолидация и ВГО"])


# ─────────────────────────────────────────────────────────────────────────────
# СОЗДАНИЕ ВГО-СВЯЗЕЙ
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/holdings/links/transactions",
    response_model=IntraGroupLinkResponse,
    summary="Связать два платежа ДДС как внутригрупповую операцию",
    description=(
        "Маркирует пару транзакций как ВГО (внутригрупповой перевод). "
        "Оба платежа получают `is_intra_group=True` и исключаются "
        "из консолидированного отчёта ДДС холдинга. "
        "Защита от дублирования: повторный вызов для той же пары → HTTP 409."
    ),
)
async def link_transactions(
    body:         IntraGroupTransactionLinkRequest,
    current_user: CurrentUser  = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
) -> IntraGroupLinkResponse:
    result = await create_intra_group_transaction_link(
        db=db,
        source_txn_id=body.source_transaction_id,
        dest_txn_id=body.destination_transaction_id,
        amount=body.amount,
    )
    return IntraGroupLinkResponse(
        link_id=result.link_id,
        source_id=result.source_id,
        destination_id=result.destination_id,
        linked_amount=result.linked_amount,
        link_type=result.link_type,
    )


@router.post(
    "/companies/holdings/links/accruals",
    response_model=IntraGroupLinkResponse,
    summary="Связать два документа начисления как внутригрупповую операцию",
    description=(
        "Маркирует пару Актов как ВГО (внутрихолдинговая купля-продажа). "
        "Оба документа получают `is_intra_group=True` и исключаются "
        "из консолидированного P&L холдинга. "
        "Защита от дублирования: повторный вызов для той же пары → HTTP 409."
    ),
)
async def link_accruals(
    body:         IntraGroupAccrualLinkRequest,
    current_user: CurrentUser  = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
) -> IntraGroupLinkResponse:
    result = await create_intra_group_accrual_link(
        db=db,
        source_doc_id=body.source_accrual_id,
        dest_doc_id=body.destination_accrual_id,
        amount=body.amount,
    )
    return IntraGroupLinkResponse(
        link_id=result.link_id,
        source_id=result.source_id,
        destination_id=result.destination_id,
        linked_amount=result.linked_amount,
        link_type=result.link_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
# КОНСОЛИДИРОВАННЫЕ ОТЧЁТЫ
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/holdings/reports/dds",
    response_model=ConsolidatedDDSResponse,
    summary="Консолидированный ДДС холдинга без ВГО",
    description=(
        "Суммирует движение денег по всем переданным company_ids за период. "
        "Возвращает два среза: "
        "'Грязный' — весь оборот (включая переводы между своими юрлицами) и "
        "'Консолидированный' — только операции с внешними контрагентами. "
        "Разница = сумма исключённых ВГО (eliminated_income / eliminated_expense)."
    ),
)
async def consolidated_dds(
    company_ids: List[UUID]    = Query(..., description="UUID компаний холдинга (повторите параметр для каждой)"),
    start_date:  date          = Query(..., description="Начало периода"),
    end_date:    date          = Query(..., description="Конец периода"),
    current_user: CurrentUser  = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
) -> ConsolidatedDDSResponse:
    result = await get_consolidated_dds_totals(
        db=db,
        company_ids=list(company_ids),
        start_date=start_date,
        end_date=end_date,
    )
    return ConsolidatedDDSResponse(
        company_ids=result.company_ids,
        start_date=result.start_date,
        end_date=result.end_date,
        dirty_income=result.dirty_income,
        dirty_expense=result.dirty_expense,
        dirty_net=result.dirty_net,
        clean_income=result.clean_income,
        clean_expense=result.clean_expense,
        clean_net=result.clean_net,
        eliminated_income=result.eliminated_income,
        eliminated_expense=result.eliminated_expense,
    )


@router.get(
    "/companies/holdings/reports/pnl",
    response_model=ConsolidatedPnLResponse,
    summary="Консолидированный P&L холдинга без ВГО (по начислениям)",
    description=(
        "Агрегирует выручку и расходы по Актам начисления "
        "всех переданных company_ids за период. "
        "Возвращает два среза: "
        "'Грязный' — все Акты (включая внутрихолдинговые реализации) и "
        "'Консолидированный' — только внешние Акты. "
        "Разница = eliminated_revenue / eliminated_expense (исключённые ВГО)."
    ),
)
async def consolidated_pnl(
    company_ids: List[UUID]    = Query(..., description="UUID компаний холдинга (повторите параметр для каждой)"),
    start_date:  date          = Query(..., description="Начало периода"),
    end_date:    date          = Query(..., description="Конец периода"),
    current_user: CurrentUser  = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
) -> ConsolidatedPnLResponse:
    result = await get_consolidated_pnl_totals(
        db=db,
        company_ids=list(company_ids),
        start_date=start_date,
        end_date=end_date,
    )
    return ConsolidatedPnLResponse(
        company_ids=result.company_ids,
        start_date=result.start_date,
        end_date=result.end_date,
        dirty_revenue=result.dirty_revenue,
        dirty_expense=result.dirty_expense,
        dirty_profit=result.dirty_profit,
        clean_revenue=result.clean_revenue,
        clean_expense=result.clean_expense,
        clean_profit=result.clean_profit,
        eliminated_revenue=result.eliminated_revenue,
        eliminated_expense=result.eliminated_expense,
    )
