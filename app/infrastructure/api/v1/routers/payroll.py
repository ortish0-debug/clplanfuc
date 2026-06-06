"""
FastAPI роутер: Зарплатный модуль ФОТ (Спринт 10).

Эндпоинты:
  POST /companies/{id}/employees                   — добавить сотрудника
  GET  /companies/{id}/employees                   — список сотрудников
  PATCH /companies/{id}/employees/{emp_id}         — обновить карточку
  POST /companies/{id}/payroll/draft               — создать/обновить черновик листка
  GET  /companies/{id}/payroll                     — реестр листков с фильтрами
  POST /companies/{id}/payroll/{calc_id}/approve   — утвердить листок (DRAFT→ACCRUED)
  POST /companies/{id}/payroll/{calc_id}/pay       — привязать выплату из ДДС
  GET  /companies/{id}/payroll/summary             — сводка долгов за месяц

Конвейер:
  create employee → create_payroll_draft → approve → link_payment_to_payroll
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.payroll import Employee, PayrollCalculation, PayrollStatus
from app.domain.schemas.payroll import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeUpdate,
    PayrollApproveResponse,
    PayrollCalculationResponse,
    PayrollDraftRequest,
    PayrollLinkPaymentRequest,
    PayrollMonthSummaryResponse,
    PayrollPaymentResponse,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanViewReports,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.payroll_service import (
    approve_payroll_calculation,
    create_payroll_draft,
    get_payroll_by_employee,
    get_payroll_debts_summary,
    link_payment_to_payroll,
)
from app.services.transaction_factory import create_expense_transaction

router = APIRouter(tags=["ФОТ — зарплатный модуль"])


# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEES — Справочник сотрудников
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить сотрудника в справочник",
)
async def create_employee(
    company_id:   UUID,
    body:         EmployeeCreate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    emp = Employee(
        id=uuid.uuid4(),
        company_id=company_id,
        name=body.name,
        position=body.position,
        base_salary=body.base_salary,
        is_active=True,
        is_deleted=False,
    )
    db.add(emp)
    await db.flush()
    return EmployeeResponse.model_validate(emp)


@router.get(
    "/companies/{company_id}/employees",
    response_model=list[EmployeeResponse],
    summary="Список сотрудников компании",
)
async def list_employees(
    company_id:   UUID,
    is_active:    Optional[bool] = Query(None, description="None = все, True = активные, False = уволенные"),
    current_user: CurrentUser    = Depends(CanViewDashboard),
    db:           AsyncSession   = Depends(get_db),
) -> list[EmployeeResponse]:
    filters = [
        Employee.company_id == company_id,
        Employee.is_deleted.is_(False),
    ]
    if is_active is not None:
        filters.append(Employee.is_active.is_(is_active))

    result = await db.execute(
        select(Employee)
        .where(and_(*filters))
        .order_by(Employee.name)
    )
    return [EmployeeResponse.model_validate(e) for e in result.scalars().all()]


@router.patch(
    "/companies/{company_id}/employees/{employee_id}",
    response_model=EmployeeResponse,
    summary="Обновить карточку сотрудника",
)
async def update_employee(
    company_id:   UUID,
    employee_id:  UUID,
    body:         EmployeeUpdate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    result = await db.execute(
        select(Employee).where(
            and_(
                Employee.id == employee_id,
                Employee.company_id == company_id,
                Employee.is_deleted.is_(False),
            )
        )
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Сотрудник {employee_id} не найден.",
        )
    if body.name is not None:
        emp.name = body.name
    if body.position is not None:
        emp.position = body.position
    if body.base_salary is not None:
        emp.base_salary = body.base_salary
    if body.is_active is not None:
        emp.is_active = body.is_active
    await db.flush()
    return EmployeeResponse.model_validate(emp)


# ─────────────────────────────────────────────────────────────────────────────
# PAYROLL DRAFT — Расчётный листок (создание / список)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/payroll/draft",
    response_model=PayrollCalculationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать или обновить черновик расчётного листка",
    description=(
        "UPSERT: если черновик за этот месяц уже существует — обновляет данные. "
        "Если листок утверждён (ACCRUED/PAID) — возвращает HTTP 409. "
        "month_date нормализуется до 1-го числа месяца."
    ),
)
async def create_draft(
    company_id:   UUID,
    body:         PayrollDraftRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> PayrollCalculationResponse:
    result = await create_payroll_draft(
        db=db,
        company_id=company_id,
        employee_id=body.employee_id,
        month_date=body.month_date,
        accrued_salary=body.accrued_salary,
        accrued_bonus=body.accrued_bonus,
        notes=body.notes,
    )
    # Перезагружаем объект для response
    calc_result = await db.execute(
        select(PayrollCalculation).where(PayrollCalculation.id == result.calc_id)
    )
    calc = calc_result.scalar_one()
    return PayrollCalculationResponse.model_validate(calc)


@router.get(
    "/companies/{company_id}/payroll",
    response_model=list[PayrollCalculationResponse],
    summary="Реестр расчётных листков с фильтрами",
)
async def list_payroll(
    company_id:    UUID,
    month_date:    Optional[date] = Query(None, description="Фильтр по месяцу (любой день месяца)"),
    employee_id:   Optional[UUID] = Query(None, description="Фильтр по сотруднику"),
    status_filter: Optional[str]  = Query(None, alias="status", description="draft | accrued | paid"),
    limit:         int  = Query(50, ge=1, le=200),
    offset:        int  = Query(0,  ge=0),
    current_user:  CurrentUser    = Depends(CanViewDashboard),
    db:            AsyncSession   = Depends(get_db),
) -> list[PayrollCalculationResponse]:
    filters = [PayrollCalculation.company_id == company_id]

    if month_date:
        filters.append(PayrollCalculation.month_date == month_date.replace(day=1))
    if employee_id:
        filters.append(PayrollCalculation.employee_id == employee_id)
    if status_filter:
        try:
            filters.append(PayrollCalculation.status == PayrollStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неверный статус: {status_filter!r}. Допустимо: draft, accrued, paid.",
            )

    result = await db.execute(
        select(PayrollCalculation)
        .where(and_(*filters))
        .order_by(PayrollCalculation.month_date.desc(), PayrollCalculation.employee_id)
        .limit(limit).offset(offset)
    )
    return [PayrollCalculationResponse.model_validate(c) for c in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# APPROVE — Утвердить к выплате (DRAFT → ACCRUED)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/payroll/{calc_id}/approve",
    response_model=PayrollApproveResponse,
    summary="Утвердить расчётный листок к выплате",
    description="Переводит статус DRAFT → ACCRUED. Сумма фиксируется и не может быть изменена.",
)
async def approve(
    company_id:   UUID,
    calc_id:      UUID,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> PayrollApproveResponse:
    calc = await approve_payroll_calculation(
        db=db,
        company_id=company_id,
        calc_id=calc_id,
    )

    # Автоматически создаём транзакцию расхода в ДДС
    employee_result = await db.execute(
        select(Employee).where(Employee.id == calc.employee_id)
    )
    employee = employee_result.scalar_one_or_none()
    emp_name = employee.name if employee else "Сотрудник"

    await create_expense_transaction(
        db=db,
        company_id=company_id,
        amount=calc.total_accrued,
        description=f"Зарплата: {emp_name} за {calc.month_date.strftime('%B %Y')}",
        payment_date=calc.month_date,
        category_name="Зарплата и ФОТ",
    )

    return PayrollApproveResponse(
        calc_id=calc.id,
        status=calc.status.value,
        message=(
            f"Расчётный листок утверждён. "
            f"К выплате: {calc.total_accrued} ₽. "
            f"Транзакция расхода создана автоматически."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PAY — Привязать выплату из ДДС
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/payroll/{calc_id}/pay",
    response_model=PayrollPaymentResponse,
    summary="Зарегистрировать выплату зарплаты",
    description=(
        "Привязывает транзакцию ДДС к расчётному листку. "
        "Увеличивает paid_amount. "
        "Если paid_amount >= total_accrued → статус автоматически становится PAID."
    ),
)
async def pay(
    company_id:   UUID,
    calc_id:      UUID,
    body:         PayrollLinkPaymentRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> PayrollPaymentResponse:
    result = await link_payment_to_payroll(
        db=db,
        company_id=company_id,
        transaction_id=body.transaction_id,
        calc_id=calc_id,
        amount=body.amount,
    )
    return PayrollPaymentResponse(
        calc_id=result.calc_id,
        new_paid_amount=result.new_paid_amount,
        outstanding=result.outstanding,
        new_status=result.new_status.value,
        txn_remaining=result.txn_remaining,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY — Сводка долгов за месяц
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/payroll/summary",
    response_model=PayrollMonthSummaryResponse,
    summary="Ведомость ФОТ — сводка долгов по зарплате за месяц",
    description=(
        "Агрегирует все расчётные листки за указанный месяц: "
        "сколько начислено, выплачено и остаток долга перед персоналом. "
        "Параметр month_date — любой день месяца (нормализуется до 1-го)."
    ),
)
async def payroll_summary(
    company_id:   UUID,
    month_date:   date         = Query(..., description="Месяц расчёта (любой день)"),
    current_user: CurrentUser  = Depends(CanViewReports),
    db:           AsyncSession = Depends(get_db),
) -> PayrollMonthSummaryResponse:
    summary = await get_payroll_debts_summary(
        db=db,
        company_id=company_id,
        month_date=month_date,
    )
    return PayrollMonthSummaryResponse(
        month_date=summary.month_date,
        employee_count=summary.employee_count,
        total_accrued=summary.total_accrued,
        total_paid=summary.total_paid,
        total_outstanding=summary.total_outstanding,
        draft_count=summary.draft_count,
        accrued_count=summary.accrued_count,
        paid_count=summary.paid_count,
    )


@router.delete(
    "/companies/{company_id}/employees/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить сотрудника",
)
async def delete_employee(
    company_id: UUID,
    employee_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить сотрудника (отметить как неактивного)."""
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == company_id
        )
    )
    employee = result.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")
    employee.is_active = False
    await db.commit()
