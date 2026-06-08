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
    model_config = {"from_attributes": True}

    id: UUID
    payment_date: date
    principal_amount: Decimal
    interest_amount: Decimal
    total_amount: Decimal = None  # alias для total_payment из ORM
    is_paid: bool

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        """Поддержка поля total_payment из ORM-модели как total_amount."""
        if hasattr(obj, "total_payment") and not hasattr(obj, "total_amount"):
            import pydantic
            d = {
                "id": obj.id,
                "payment_date": obj.payment_date,
                "principal_amount": obj.principal_amount,
                "interest_amount": obj.interest_amount,
                "total_amount": obj.total_payment,
                "is_paid": obj.is_paid,
            }
            return cls(**d)
        return super().model_validate(obj, **kwargs)


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
