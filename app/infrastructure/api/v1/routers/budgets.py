"""
FastAPI роутер: Бюджеты и отчёт «План vs Факт» (Спринт 6).

Эндпоинты:
  GET  /companies/{id}/budgets/plan-fact   — отчёт план-факт (CanViewReports)
  POST /companies/{id}/budgets/save        — UPSERT бюджета (CanWriteFinance)
  GET  /companies/{id}/budgets             — список бюджетов за период (CanViewReports)

UPSERT-логика:
  Бюджет уникален по (company_id, category_id, project_id, year, month).
  Поскольку project_id может быть NULL и PostgreSQL считает NULL != NULL
  в unique-ограничениях, используем ручной SELECT → UPDATE/INSERT
  вместо ON CONFLICT, что корректно обрабатывает оба случая.
"""
from __future__ import annotations

import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.projects import Budget
from app.domain.schemas.projects import (
    BudgetResponse,
    BudgetSaveRequest,
    CategoryPlanFactResponse,
    PlanFactReportResponse,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewReports,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.budget_service import PlanFactReport, get_plan_fact_report

router = APIRouter(tags=["Бюджеты"])


# ─────────────────────────────────────────────────────────────────────────────
# КОНВЕРТЕР: сервисный датакласс → Pydantic-схема
# ─────────────────────────────────────────────────────────────────────────────


def _to_plan_fact_response(report: PlanFactReport) -> PlanFactReportResponse:
    """Конвертирует датакласс PlanFactReport в Pydantic-схему ответа."""
    rows = [
        CategoryPlanFactResponse(
            category_id=r.category_id,
            category_name=r.category_name,
            category_type=r.category_type,
            parent_id=r.parent_id,
            depth=r.depth,
            plan_amount=r.plan_amount,
            fact_amount=r.fact_amount,
            deviation=r.deviation,
            execution_pct=r.execution_pct,
            has_gap=r.has_gap,
        )
        for r in report.rows
    ]
    return PlanFactReportResponse(
        company_id=report.company_id,
        year=report.year,
        month=report.month,
        project_id=report.project_id,
        rows=rows,
        total_income_plan=report.total_income_plan,
        total_income_fact=report.total_income_fact,
        total_expense_plan=report.total_expense_plan,
        total_expense_fact=report.total_expense_fact,
        net_plan=report.net_plan,
        net_fact=report.net_fact,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ПЛАН-ФАКТ
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/budgets/plan-fact",
    response_model=PlanFactReportResponse,
    summary="Отчёт «План vs Факт» за месяц",
    description=(
        "Для каждой категории показывает плановую сумму из таблицы budgets "
        "и фактическую сумму транзакций по дате начисления (accrual_date). "
        "Доступно ролям: Owner, Admin, Accountant, Viewer."
    ),
)
async def get_plan_fact(
    company_id:   UUID,
    year:         int            = Query(..., ge=2000, le=2100, description="Год"),
    month:        int            = Query(..., ge=1, le=12,      description="Месяц (1–12)"),
    project_id:   Optional[UUID] = Query(None, description="Фильтр по проекту (опционально)"),
    current_user: CurrentUser    = Depends(CanViewReports),
    db:           AsyncSession   = Depends(get_db),
) -> PlanFactReportResponse:
    report = await get_plan_fact_report(
        db=db,
        company_id=company_id,
        year=year,
        month=month,
        project_id=project_id,
    )
    return _to_plan_fact_response(report)


# ─────────────────────────────────────────────────────────────────────────────
# UPSERT БЮДЖЕТА
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/budgets/save",
    response_model=BudgetResponse,
    status_code=status.HTTP_200_OK,
    summary="Сохранить / обновить плановую сумму бюджета",
    description=(
        "UPSERT: создаёт запись, если она не существует, или обновляет plan_amount "
        "существующей. Уникальность по (category_id, project_id, year, month). "
        "Корректно обрабатывает project_id=NULL (общий бюджет без проекта)."
    ),
)
async def save_budget(
    company_id:   UUID,
    body:         BudgetSaveRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BudgetResponse:
    # ── Формируем условие поиска с учётом NULL project_id ──────────────────
    # PostgreSQL: NULL != NULL в unique-ограничениях, поэтому не используем
    # ON CONFLICT и вместо этого делаем явный SELECT.
    lookup_filters = [
        Budget.company_id  == company_id,
        Budget.category_id == body.category_id,
        Budget.year        == body.year,
        Budget.month       == body.month,
    ]
    if body.project_id is not None:
        lookup_filters.append(Budget.project_id == body.project_id)
    else:
        lookup_filters.append(Budget.project_id.is_(None))

    result   = await db.execute(select(Budget).where(and_(*lookup_filters)))
    existing = result.scalar_one_or_none()

    if existing:
        # UPDATE: меняем только plan_amount
        existing.plan_amount = body.plan_amount
        await db.flush()
        return BudgetResponse(
            id=existing.id,
            company_id=existing.company_id,
            category_id=existing.category_id,
            project_id=existing.project_id,
            year=existing.year,
            month=existing.month,
            plan_amount=existing.plan_amount,
            is_new=False,
        )

    # INSERT: новая запись
    budget = Budget(
        id=uuid.uuid4(),
        company_id=company_id,
        category_id=body.category_id,
        project_id=body.project_id,
        year=body.year,
        month=body.month,
        plan_amount=body.plan_amount,
    )
    db.add(budget)
    await db.flush()
    return BudgetResponse(
        id=budget.id,
        company_id=budget.company_id,
        category_id=budget.category_id,
        project_id=budget.project_id,
        year=budget.year,
        month=budget.month,
        plan_amount=budget.plan_amount,
        is_new=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# СПИСОК БЮДЖЕТОВ (справочник)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/budgets",
    response_model=list[BudgetResponse],
    summary="Список бюджетных строк за период",
    description="Возвращает все бюджеты компании за указанный год/месяц.",
)
async def list_budgets(
    company_id:   UUID,
    year:         int            = Query(..., ge=2000, le=2100),
    month:        int            = Query(..., ge=1, le=12),
    project_id:   Optional[UUID] = Query(None),
    current_user: CurrentUser    = Depends(CanViewReports),
    db:           AsyncSession   = Depends(get_db),
) -> list[BudgetResponse]:
    filters = [
        Budget.company_id == company_id,
        Budget.year       == year,
        Budget.month      == month,
    ]
    if project_id is not None:
        filters.append(Budget.project_id == project_id)

    result = await db.execute(
        select(Budget).where(and_(*filters)).order_by(Budget.category_id)
    )
    return [
        BudgetResponse(
            id=b.id,
            company_id=b.company_id,
            category_id=b.category_id,
            project_id=b.project_id,
            year=b.year,
            month=b.month,
            plan_amount=b.plan_amount,
            is_new=False,
        )
        for b in result.scalars().all()
    ]
