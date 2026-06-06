"""Управление кредитами и займами (Sprint 19)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.assets_loans import Loan, LoanPaymentSchedule, LoanType
from app.services.ledger_service import post_double_entry


async def create_loan_and_schedule(
    db: AsyncSession,
    company_id: UUID,
    contract_number: str,
    type_: LoanType,
    principal_amount: Decimal,
    interest_rate: Decimal,
    start_date: date,
    duration_months: int,
    counterpart_id: Optional[UUID] = None,
) -> Loan:
    """Создаёт договор и график платежей. Генерирует стартовую проводку."""
    principal_amount = Decimal(str(principal_amount))
    interest_rate = Decimal(str(interest_rate))

    contract = Loan(
        id=uuid.uuid4(),
        company_id=company_id,
        counterparty_id=counterpart_id,
        name=contract_number,
        loan_type=type_,
        total_amount=principal_amount,
        interest_rate=interest_rate,
        start_date=start_date,
        term_months=duration_months,
        remaining_principal=principal_amount,
        is_active=True,
    )
    db.add(contract)
    await db.flush()

    monthly_principal = principal_amount / Decimal(duration_months)
    monthly_interest = (principal_amount * (interest_rate / Decimal(100))) / Decimal(12)
    quantum = Decimal("0.01")

    for month in range(duration_months):
        payment_date = start_date + timedelta(days=30 * (month + 1))

        if month == duration_months - 1:
            total_principal = principal_amount - (monthly_principal * Decimal(month)).quantize(quantum, rounding=ROUND_HALF_UP)
            total_interest = (monthly_interest * Decimal(duration_months)).quantize(quantum, rounding=ROUND_HALF_UP) - (
                monthly_interest * Decimal(month)
            ).quantize(quantum, rounding=ROUND_HALF_UP)
        else:
            total_principal = monthly_principal.quantize(quantum, rounding=ROUND_HALF_UP)
            total_interest = monthly_interest.quantize(quantum, rounding=ROUND_HALF_UP)

        schedule = LoanPaymentSchedule(
            id=uuid.uuid4(),
            loan_id=contract.id,
            payment_date=payment_date,
            principal_amount=total_principal,
            interest_amount=total_interest,
            total_payment=total_principal + total_interest,
            is_paid=False,
        )
        db.add(schedule)

    await db.flush()

    # Проводки отключены (счета не созданы в базе)
    # await post_double_entry(
    #     db=db,
    #     company_id=company_id,
    #     date_=start_date,
    #     description=f"Получение {type_.value}: договор {contract_number}",
    #     doc_type="loan_contract",
    #     doc_id=contract.id,
    #     postings=[
    #         {"code": "5100", "debit": principal_amount, "credit": Decimal(0)},
    #         {"code": "6700", "debit": Decimal(0), "credit": principal_amount},
    #     ],
    # )

    return contract


async def make_schedule_payment(
    db: AsyncSession,
    company_id: UUID,
    schedule_id: UUID,
) -> LoanPaymentSchedule:
    """Отмечает платёж и генерирует проводку выплаты."""
    result = await db.execute(
        select(LoanPaymentSchedule).where(LoanPaymentSchedule.id == schedule_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    contract_result = await db.execute(
        select(Loan).where(
            and_(
                Loan.id == schedule.loan_id,
                Loan.company_id == company_id,
            )
        )
    )
    contract = contract_result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this loan")

    schedule.is_paid = True
    await db.flush()

    await post_double_entry(
        db=db,
        company_id=company_id,
        date_=schedule.payment_date,
        description=f"Выплата по договору {contract.contract_number}",
        doc_type="loan_payment",
        doc_id=schedule.id,
        postings=[
            {"code": "6700", "debit": schedule.principal_amount, "credit": Decimal(0)},
            {"code": "9102", "debit": schedule.interest_amount, "credit": Decimal(0)},
            {"code": "5100", "debit": Decimal(0), "credit": schedule.total_amount},
        ],
    )

    return schedule
