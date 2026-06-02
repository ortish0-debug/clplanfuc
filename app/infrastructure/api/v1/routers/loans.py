"""API: Кредиты и займы (Sprint 19)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.assets_loans import Loan, LoanPaymentSchedule
from app.domain.schemas.loans import LoanContractCreate, LoanContractResponse, LoanPaymentScheduleResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.audit_service import log_audit_action
from app.services.loan_service import create_loan_and_schedule, make_schedule_payment

router = APIRouter(tags=["Кредиты и займы"])


@router.post(
    "/companies/{company_id}/loans",
    response_model=LoanContractResponse,
)
async def create_loan(
    company_id: UUID,
    req: LoanContractCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> LoanContractResponse:
    """Создать кредитный договор и сгенерировать график платежей."""
    print(f"DEBUG: Got request: {req.model_dump()}")
    contract = await create_loan_and_schedule(
        db=db,
        company_id=company_id,
        contract_number=req.contract_number,
        type_=req.type,
        principal_amount=req.principal_amount,
        interest_rate=req.interest_rate,
        start_date=req.start_date,
        duration_months=req.duration_months,
        counterpart_id=req.counterpart_id,
    )
    await db.commit()
    print(f"DEBUG: Contract created: {contract.__dict__}")
    try:
        response = LoanContractResponse.model_validate(contract)
        print(f"DEBUG: Response created: {response.model_dump()}")
        return response
    except Exception as e:
        print(f"DEBUG: Response validation error: {e}")
        raise


@router.get(
    "/companies/{company_id}/loans",
    response_model=list[LoanContractResponse],
)
async def list_loans(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[LoanContractResponse]:
    """Получить список договоров компании."""
    result = await db.execute(
        select(Loan).where(Loan.company_id == company_id).order_by(Loan.created_at.desc())
    )
    contracts = result.scalars().all()
    return [LoanContractResponse.model_validate(c) for c in contracts]


@router.get(
    "/companies/{company_id}/loans/{contract_id}/schedule",
    response_model=list[LoanPaymentScheduleResponse],
)
async def get_loan_schedule(
    company_id: UUID,
    contract_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[LoanPaymentScheduleResponse]:
    """Получить график платежей по договору."""
    contract_result = await db.execute(
        select(Loan).where(
            Loan.id == contract_id,
            Loan.company_id == company_id,
        )
    )
    if not contract_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")

    result = await db.execute(
        select(LoanPaymentSchedule).where(
            LoanPaymentSchedule.loan_id == contract_id
        ).order_by(LoanPaymentSchedule.payment_date)
    )
    schedules = result.scalars().all()
    return [LoanPaymentScheduleResponse.model_validate(s) for s in schedules]


@router.post(
    "/companies/{company_id}/loans/schedule/{schedule_id}/pay",
    response_model=LoanPaymentScheduleResponse,
)
async def pay_schedule(
    company_id: UUID,
    schedule_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> LoanPaymentScheduleResponse:
    """Провести оплату по траншу графика."""
    schedule = await make_schedule_payment(db=db, company_id=company_id, schedule_id=schedule_id)

    # ── Логируем платёж в audit trail ────────────────────────────────────
    ip_address = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.id,
        action="loan.pay_installment",
        target_type="loan_payment_schedule",
        target_id=schedule_id,
        ip_address=ip_address,
    )

    await db.commit()
    return LoanPaymentScheduleResponse.model_validate(schedule)


@router.delete(
    "/companies/{company_id}/loans/{contract_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить займ",
)
async def delete_loan(
    company_id: UUID,
    contract_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить займ (мягкое удаление)."""
    result = await db.execute(
        select(Loan).where(
            Loan.id == contract_id,
            Loan.company_id == company_id
        )
    )
    loan = result.scalar_one_or_none()
    if not loan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Займ не найден")
    loan.is_deleted = True
    await db.commit()
