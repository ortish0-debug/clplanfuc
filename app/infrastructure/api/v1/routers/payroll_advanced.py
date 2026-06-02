"""Payroll advanced: tax calculations."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.services.payroll_tax_service import calculate_ru_payroll_taxes

router = APIRouter(
    prefix="/companies/{company_id}/payroll",
    tags=["Payroll"],
)


class PayrollTaxRequest(BaseModel):
    """Salary tax calculation request."""

    salary: float
    is_smb: bool = True


class PayrollTaxResponse(BaseModel):
    """Salary tax calculation response."""

    ndfl: float
    contributions: float
    net_salary: float


@router.post("/calculate", response_model=PayrollTaxResponse, status_code=200)
async def calculate_payroll_taxes(
    company_id: UUID,
    request: PayrollTaxRequest,
    current_user=Depends(CanWriteFinance),
) -> PayrollTaxResponse:
    """Calculate NDFL + contributions for salary."""
    result = calculate_ru_payroll_taxes(request.salary, request.is_smb)
    return PayrollTaxResponse(**result)
