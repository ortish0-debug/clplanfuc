"""
Pydantic v2 схемы: Зарплатный модуль ФОТ (Спринт 10).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(
        lambda v: str(v.quantize(Decimal("0.01"))),
        return_type=str,
        when_used="json",
    ),
]

OptionalDecimalStr = Annotated[
    Optional[Decimal],
    PlainSerializer(
        lambda v: str(v.quantize(Decimal("0.01"))) if v is not None else None,
        return_type=Optional[str],
        when_used="json",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEE
# ─────────────────────────────────────────────────────────────────────────────


class EmployeeCreate(BaseModel):
    name:        str           = Field(..., min_length=1, max_length=512, description="ФИО сотрудника")
    position:    Optional[str] = Field(None, max_length=255,              description="Должность")
    base_salary: Optional[Decimal] = Field(None, ge=0,                   description="Оклад по договору, ₽")


class EmployeeUpdate(BaseModel):
    name:        Optional[str]     = Field(None, min_length=1, max_length=512)
    position:    Optional[str]     = Field(None, max_length=255)
    base_salary: Optional[Decimal] = Field(None, ge=0)
    is_active:   Optional[bool]    = None


class EmployeeResponse(BaseModel):
    id:          UUID
    company_id:  UUID
    name:        str
    position:    Optional[str]
    base_salary: OptionalDecimalStr
    is_active:   bool
    created_at:  datetime
    updated_at:  datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# PAYROLL CALCULATION
# ─────────────────────────────────────────────────────────────────────────────


class PayrollDraftRequest(BaseModel):
    employee_id:    UUID    = Field(..., description="UUID сотрудника")
    month_date:     date    = Field(..., description="Месяц расчёта (любой день — нормализуется до 1-го)")
    accrued_salary: Decimal = Field(..., ge=0, description="Оклад за месяц, ₽")
    accrued_bonus:  Decimal = Field(Decimal("0.00"), ge=0, description="Премия / надбавка, ₽")
    notes:          Optional[str] = Field(None, max_length=2048, description="Комментарий бухгалтера")


class PayrollLinkPaymentRequest(BaseModel):
    transaction_id: UUID    = Field(..., description="UUID транзакции-выплаты из ДДС")
    amount:         Decimal = Field(..., gt=0, description="Сумма выплаты, ₽")


class PayrollCalculationResponse(BaseModel):
    id:             UUID
    company_id:     UUID
    employee_id:    UUID
    month_date:     date
    accrued_salary: DecimalStr
    accrued_bonus:  DecimalStr
    total_accrued:  DecimalStr
    paid_amount:    DecimalStr
    status:         str
    notes:          Optional[str]
    created_at:     datetime
    updated_at:     datetime

    model_config = {"from_attributes": True}


class PayrollApproveResponse(BaseModel):
    calc_id:    UUID
    status:     str
    message:    str


class PayrollPaymentResponse(BaseModel):
    calc_id:         UUID
    new_paid_amount: DecimalStr
    outstanding:     DecimalStr
    new_status:      str
    txn_remaining:   DecimalStr


# ─────────────────────────────────────────────────────────────────────────────
# MONTH SUMMARY
# ─────────────────────────────────────────────────────────────────────────────


class PayrollMonthSummaryResponse(BaseModel):
    """Сводка ФОТ за месяц по всем сотрудникам компании."""
    month_date:        date
    employee_count:    int
    total_accrued:     DecimalStr
    total_paid:        DecimalStr
    total_outstanding: DecimalStr
    draft_count:       int
    accrued_count:     int
    paid_count:        int
