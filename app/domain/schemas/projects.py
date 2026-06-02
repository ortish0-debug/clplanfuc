"""
Pydantic v2 схемы для Проектов, Бюджетов и отчёта «План vs Факт».
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT
# ─────────────────────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name:        str            = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    color:       Optional[str] = Field(None, max_length=7, description="#RRGGBB")


class ProjectUpdate(BaseModel):
    name:        Optional[str]  = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    color:       Optional[str] = Field(None, max_length=7)
    is_active:   Optional[bool] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          UUID
    company_id:  UUID
    name:        str
    description: Optional[str]
    color:       Optional[str]
    is_active:   bool


# ─────────────────────────────────────────────────────────────────────────────
# BUDGET SAVE (UPSERT)
# ─────────────────────────────────────────────────────────────────────────────


class BudgetSaveRequest(BaseModel):
    category_id: UUID
    project_id:  Optional[UUID] = Field(
        None,
        description="Привязка к проекту. NULL — общий бюджет без проекта.",
    )
    year:        int            = Field(..., ge=2000, le=2100)
    month:       int            = Field(..., ge=1, le=12)
    plan_amount: Decimal        = Field(..., ge=0, description="Плановая сумма ≥ 0")


class BudgetResponse(BaseModel):
    id:          UUID
    company_id:  UUID
    category_id: UUID
    project_id:  Optional[UUID]
    year:        int
    month:       int
    plan_amount: DecimalStr
    is_new:      bool = Field(
        ...,
        description="True — запись создана, False — обновлена существующая.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ПЛАН-ФАКТ ОТЧЁТ
# ─────────────────────────────────────────────────────────────────────────────


class CategoryPlanFactResponse(BaseModel):
    """Одна строка отчёта по категории."""
    category_id:   UUID
    category_name: str
    category_type: str          # "income" | "expense"
    parent_id:     Optional[UUID]
    depth:         int

    plan_amount:   DecimalStr
    fact_amount:   DecimalStr
    deviation:     DecimalStr   # fact − plan
    execution_pct: DecimalStr   # fact / plan × 100 (0 если plan=0)
    has_gap:       bool         # перерасход (expense) или недопоступление (income)


class PlanFactReportResponse(BaseModel):
    """Полный отчёт «План vs Факт» за месяц."""
    company_id: UUID
    year:       int
    month:      int
    project_id: Optional[UUID]

    rows: list[CategoryPlanFactResponse]

    # Итоги по разделам
    total_income_plan:   DecimalStr
    total_income_fact:   DecimalStr
    total_expense_plan:  DecimalStr
    total_expense_fact:  DecimalStr
    net_plan:            DecimalStr   # доходы - расходы (план)
    net_fact:            DecimalStr   # доходы - расходы (факт)
