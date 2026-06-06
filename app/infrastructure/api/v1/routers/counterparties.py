"""
FastAPI роутер: контрагенты (Counterparty) и счета (ContractOrInvoice).

Эндпоинты:
  GET    /companies/{id}/counterparties              — список контрагентов
  POST   /companies/{id}/counterparties              — создать контрагента
  PATCH  /companies/{id}/counterparties/{cp_id}      — обновить
  DELETE /companies/{id}/counterparties/{cp_id}      — мягкое удаление
  GET    /companies/{id}/counterparties/debts        — сводка долгов
  GET    /companies/{id}/invoices                    — список счетов
  POST   /companies/{id}/invoices                    — создать счёт
  PATCH  /companies/{id}/invoices/{inv_id}           — обновить счёт
  POST   /companies/{id}/invoices/{inv_id}/pay       — зафиксировать оплату
  GET    /companies/{id}/reports/balance-sheet       — Управленческий Баланс
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.counterparties import (
    ContractOrInvoice,
    Counterparty,
    InvoiceStatus,
    InvoiceType,
)
from app.domain.schemas.balance_sheet import (
    BalanceSheetResponse,
    CounterpartyCreate,
    CounterpartyResponse,
    CounterpartyUpdate,
    DebtSummaryResponse,
    InvoiceCreate,
    InvoicePaymentRequest,
    InvoiceResponse,
)
from app.infrastructure.api.v1.dependencies.auth import CanViewReports, CanWriteFinance, CanWriteOperational, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.balance_sheet_calculator import calculate_balance_sheet, calculate_debt_summary

router = APIRouter(tags=["Контрагенты и баланс"])


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_cp(db: AsyncSession, cp_id: UUID, company_id: UUID) -> Counterparty:
    r = await db.execute(
        select(Counterparty).where(
            and_(Counterparty.id == cp_id, Counterparty.company_id == company_id,
                 Counterparty.is_deleted.is_(False))
        )
    )
    cp = r.scalar_one_or_none()
    if not cp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Контрагент {cp_id} не найден.")
    return cp


async def _get_inv(db: AsyncSession, inv_id: UUID, company_id: UUID) -> ContractOrInvoice:
    r = await db.execute(
        select(ContractOrInvoice).where(
            and_(ContractOrInvoice.id == inv_id,
                 ContractOrInvoice.company_id == company_id,
                 ContractOrInvoice.is_deleted.is_(False))
        )
    )
    inv = r.scalar_one_or_none()
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Счёт {inv_id} не найден.")
    return inv


def _inv_to_response(inv: ContractOrInvoice, cp_name: Optional[str] = None) -> InvoiceResponse:
    return InvoiceResponse(
        id=inv.id,
        company_id=inv.company_id,
        counterparty_id=inv.counterparty_id,
        counterparty_name=cp_name,
        number=inv.number,
        date=inv.date,
        due_date=inv.due_date,
        total_amount=inv.total_amount,
        paid_amount=inv.paid_amount,
        outstanding_amount=inv.total_amount - inv.paid_amount,
        status=inv.status.value,
        invoice_type=inv.invoice_type.value,
        description=inv.description,
        currency=inv.currency.value,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTIES
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/counterparties",
    response_model=list[CounterpartyResponse],
    summary="Список контрагентов",
)
async def list_counterparties(
    company_id: UUID,
    is_customer: Optional[bool] = Query(None),
    is_supplier: Optional[bool] = Query(None),
    current_user: CurrentUser = Depends(CanViewReports),
    db: AsyncSession = Depends(get_db),
) -> list[CounterpartyResponse]:
    filters = [
        Counterparty.company_id == company_id,
        Counterparty.is_active.is_(True),
        Counterparty.is_deleted.is_(False),
    ]
    if is_customer is not None:
        filters.append(Counterparty.is_customer.is_(is_customer))
    if is_supplier is not None:
        filters.append(Counterparty.is_supplier.is_(is_supplier))

    result = await db.execute(
        select(Counterparty).where(and_(*filters)).order_by(Counterparty.name)
    )
    return [CounterpartyResponse.model_validate(cp) for cp in result.scalars().all()]


@router.post(
    "/companies/{company_id}/counterparties",
    response_model=CounterpartyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать контрагента",
)
async def create_counterparty(
    company_id: UUID,
    body: CounterpartyCreate,
    current_user: CurrentUser = Depends(CanWriteOperational),
    db: AsyncSession = Depends(get_db),
) -> CounterpartyResponse:
    if not body.is_customer and not body.is_supplier:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Контрагент должен быть покупателем (is_customer) или поставщиком (is_supplier).",
        )
    cp = Counterparty(
        id=uuid.uuid4(),
        company_id=company_id,
        **body.model_dump(),
        is_active=True,
        is_deleted=False,
    )
    db.add(cp)
    await db.flush()
    return CounterpartyResponse.model_validate(cp)


@router.patch(
    "/companies/{company_id}/counterparties/{cp_id}",
    response_model=CounterpartyResponse,
    summary="Обновить контрагента",
)
async def update_counterparty(
    company_id: UUID,
    cp_id: UUID,
    body: CounterpartyUpdate,
    current_user: CurrentUser = Depends(CanWriteOperational),
    db: AsyncSession = Depends(get_db),
) -> CounterpartyResponse:
    cp = await _get_cp(db, cp_id, company_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(cp, field, value)
    await db.flush()
    return CounterpartyResponse.model_validate(cp)


@router.delete(
    "/companies/{company_id}/counterparties/{cp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить контрагента",
)
async def delete_counterparty(
    company_id: UUID,
    cp_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    cp = await _get_cp(db, cp_id, company_id)
    cp.is_deleted = True
    cp.deleted_at = datetime.now(tz=timezone.utc)
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# DEBTS SUMMARY
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/counterparties/debts",
    response_model=DebtSummaryResponse,
    summary="Сводка долгов контрагентов (дебиторка / кредиторка)",
)
async def get_debts(
    company_id: UUID,
    as_of: date = Query(default_factory=date.today, description="Дата среза"),
    top_n: int  = Query(default=5, ge=1, le=50),
    current_user: CurrentUser = Depends(CanViewReports),
    db: AsyncSession = Depends(get_db),
) -> DebtSummaryResponse:
    from app.domain.models.finance import Company
    company = await db.get(Company, company_id)
    currency = company.currency.value if company else "RUB"
    return await calculate_debt_summary(company_id, as_of, db, currency, top_n)


# ─────────────────────────────────────────────────────────────────────────────
# INVOICES
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/invoices",
    response_model=list[InvoiceResponse],
    summary="Список счетов (дебиторка / кредиторка)",
)
async def list_invoices(
    company_id: UUID,
    invoice_type: Optional[str] = Query(None, description="customer_invoice | supplier_bill"),
    status_filter: Optional[str] = Query(None, alias="status", description="pending | partially_paid | paid"),
    current_user: CurrentUser = Depends(CanViewReports),
    db: AsyncSession = Depends(get_db),
) -> list[InvoiceResponse]:
    filters = [
        ContractOrInvoice.company_id == company_id,
        ContractOrInvoice.is_deleted.is_(False),
    ]
    if invoice_type:
        try:
            filters.append(ContractOrInvoice.invoice_type == InvoiceType(invoice_type))
        except ValueError:
            pass
    if status_filter:
        try:
            filters.append(ContractOrInvoice.status == InvoiceStatus(status_filter))
        except ValueError:
            pass

    result = await db.execute(
        select(ContractOrInvoice, Counterparty.name.label("cp_name"))
        .join(Counterparty, ContractOrInvoice.counterparty_id == Counterparty.id)
        .where(and_(*filters))
        .order_by(ContractOrInvoice.date.desc())
    )
    return [_inv_to_response(row.ContractOrInvoice, row.cp_name) for row in result.all()]


@router.post(
    "/companies/{company_id}/invoices",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать счёт / акт",
)
async def create_invoice(
    company_id: UUID,
    body: InvoiceCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    # Проверяем принадлежность контрагента
    cp = await _get_cp(db, body.counterparty_id, company_id)

    try:
        inv_type = InvoiceType(body.invoice_type)
    except ValueError:
        raise HTTPException(422, detail=f"Неверный тип счёта: {body.invoice_type}")

    from app.domain.models.finance import Currency
    try:
        currency = Currency(body.currency)
    except ValueError:
        currency = Currency.RUB

    inv = ContractOrInvoice(
        id=uuid.uuid4(),
        company_id=company_id,
        counterparty_id=body.counterparty_id,
        number=body.number,
        date=body.date,
        due_date=body.due_date,
        description=body.description,
        total_amount=body.total_amount,
        paid_amount=Decimal("0.00"),
        status=InvoiceStatus.PENDING,
        invoice_type=inv_type,
        currency=currency,
        is_deleted=False,
    )
    db.add(inv)
    await db.flush()
    return _inv_to_response(inv, cp.name)


@router.post(
    "/companies/{company_id}/invoices/{inv_id}/pay",
    response_model=InvoiceResponse,
    summary="Зафиксировать оплату счёта",
    description="Увеличивает paid_amount и автоматически обновляет статус счёта.",
)
async def pay_invoice(
    company_id: UUID,
    inv_id: UUID,
    body: InvoicePaymentRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> InvoiceResponse:
    inv = await _get_inv(db, inv_id, company_id)
    if inv.status == InvoiceStatus.CANCELLED:
        raise HTTPException(422, detail="Нельзя оплачивать отменённый счёт.")

    inv.paid_amount = min(inv.paid_amount + body.payment_amount, inv.total_amount)

    if inv.paid_amount >= inv.total_amount:
        inv.status = InvoiceStatus.PAID
    elif inv.paid_amount > Decimal("0.00"):
        inv.status = InvoiceStatus.PARTIALLY_PAID

    # Подтягиваем имя контрагента
    cp_result = await db.execute(
        select(Counterparty.name).where(Counterparty.id == inv.counterparty_id)
    )
    cp_name = cp_result.scalar_one_or_none()
    await db.flush()
    return _inv_to_response(inv, cp_name)


# ─────────────────────────────────────────────────────────────────────────────
# BALANCE SHEET
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/reports/balance-sheet",
    response_model=BalanceSheetResponse,
    summary="Управленческий Баланс (Balance Sheet)",
    description="Структура активов и пассивов компании на указанную дату. Проверяет балансовое равенство.",
)
async def get_balance_sheet(
    company_id: UUID,
    on_date: date = Query(default_factory=date.today, description="Дата среза (YYYY-MM-DD)"),
    current_user: CurrentUser = Depends(CanViewReports),
    db: AsyncSession = Depends(get_db),
) -> BalanceSheetResponse:
    from app.domain.models.finance import Company
    company = await db.get(Company, company_id)
    currency = company.currency.value if company else "RUB"
    return await calculate_balance_sheet(company_id, on_date, db, currency)
