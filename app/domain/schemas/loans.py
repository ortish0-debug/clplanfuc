"""Pydantic-схемы для кредитов и займов (Sprint 19)."""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.domain.models.assets_loans import LoanType


class LoanContractCreate(BaseModel):
    contract_number: str
    type: LoanType
    principal_amount: Decimal
    interest_rate: Decimal
    start_date: date
    duration_months: int
    counterpart_id: Optional[UUID] = None


class LoanPaymentScheduleResponse(BaseModel):
    id: UUID
    payment_date: date
    principal_amount: Decimal
    interest_amount: Decimal
    total_amount: Decimal
    is_paid: bool


class LoanContractResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    company_id: UUID
    name: str
    loan_type: LoanType
    total_amount: Decimal
    interest_rate: Decimal
    start_date: date
    term_months: int
    remaining_principal: Decimal
    is_active: bool
    counterparty_id: Optional[UUID] = None
