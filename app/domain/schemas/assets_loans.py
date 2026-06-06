"""
Pydantic v2 схемы для Основных Средств и Кредитов.
"""
from __future__ import annotations

from datetime import date, datetime
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
# ASSETS — Основные средства
# ─────────────────────────────────────────────────────────────────────────────


class AssetCreate(BaseModel):
    name:                str            = Field(..., min_length=1, max_length=255)
    description:         Optional[str]  = None
    purchase_cost:       Decimal        = Field(..., gt=0, description="Первоначальная стоимость")
    purchase_date:       date
    amortization_months: int            = Field(..., ge=1, description="Срок СПИ в месяцах")
    account_id:          Optional[UUID] = Field(None, description="Счёт оплаты при покупке")


class AssetResponse(BaseModel):
    """ОС со всеми вычислимыми полями."""
    id:                       UUID
    company_id:               UUID
    name:                     str
    description:              Optional[str]
    purchase_cost:            DecimalStr
    purchase_date:            date
    amortization_months:      int
    accumulated_amortization: DecimalStr
    account_id:               Optional[UUID]
    is_active:                bool
    # Вычислимые (заполняются в роутере из model properties)
    monthly_amortization:     DecimalStr
    residual_value:           DecimalStr
    is_fully_amortized:       bool


class AmortizeRequest(BaseModel):
    amortization_date: date = Field(
        default_factory=date.today,
        description="Дата начисления (обычно 1-е число текущего месяца)",
    )


class AmortizationResultResponse(BaseModel):
    """Результат начисления амортизации по одному ОС."""
    asset_id:           UUID
    asset_name:         str
    month_label:        str
    amount:             DecimalStr
    accumulated:        DecimalStr
    residual_value:     DecimalStr
    is_fully_amortized: bool
    transaction_id:     Optional[UUID]
    skipped:            bool


# ─────────────────────────────────────────────────────────────────────────────
# LOANS — Кредиты и займы
# ─────────────────────────────────────────────────────────────────────────────


class LoanCreate(BaseModel):
    name:            str            = Field(..., min_length=1, max_length=255)
    counterparty_id: Optional[UUID] = Field(None, description="Кредитор из справочника")
    account_id:      Optional[UUID] = Field(None, description="Счёт поступления средств")
    total_amount:    Decimal        = Field(..., gt=0,  description="Сумма кредита")
    interest_rate:   Decimal        = Field(..., ge=0,  description="Годовая ставка (%)")
    start_date:      date           = Field(...,         description="Дата выдачи")
    term_months:     int            = Field(..., ge=1,  description="Срок в месяцах")
    loan_type:       str            = Field(
        ...,
        description="Тип: annuity | differentiated",
        pattern="^(annuity|differentiated)$",
    )


class LoanResponse(BaseModel):
    """Кредит со сводной аналитикой из LoanSummary."""
    id:                  UUID
    company_id:          UUID
    name:                str
    counterparty_id:     Optional[UUID]
    account_id:          Optional[UUID]
    total_amount:        DecimalStr
    interest_rate:       DecimalStr
    start_date:          date
    term_months:         int
    loan_type:           str
    remaining_principal: DecimalStr
    is_active:           bool
    # Из LoanSummary
    paid_principal:      DecimalStr
    total_interest_paid: DecimalStr
    total_interest_plan: DecimalStr
    next_payment_date:   Optional[date]
    next_payment_amount: Optional[DecimalStr]
    periods_paid:        int
    periods_total:       int


class ScheduleEntryResponse(BaseModel):
    """Одна строка графика платежей."""
    id:               UUID
    loan_id:          UUID
    payment_number:   int           # порядковый номер (1-based)
    payment_date:     date
    principal_amount: DecimalStr
    interest_amount:  DecimalStr
    total_payment:    DecimalStr
    is_paid:          bool
    paid_at:          Optional[datetime]
    transaction_id:   Optional[UUID]
