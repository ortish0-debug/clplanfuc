"""
FastAPI роутер: Документы начислений и взаиморасчёты (Спринт 9).

Эндпоинты:
  POST   /companies/{id}/accrual-documents             — создать Акт/Реализацию/Накладную
  GET    /companies/{id}/accrual-documents             — реестр документов с фильтрами
  GET    /companies/{id}/accrual-documents/{doc_id}    — карточка документа + история платежей
  POST   /companies/{id}/accruals/link                 — привязать платёж к документу
  DELETE /companies/{id}/accruals/link/{link_id}       — снять привязку
  GET    /companies/{id}/counterparties/balances       — ведомость дебиторки/кредиторки

Метод начислений (Accrual Basis):
  AccrualDocument — первичный документ, порождающий обязательство.
  TransactionAccrualLink — факт частичной или полной оплаты документа платежом.
  SUM(links) == doc.amount → doc.payment_status = 'paid'.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.accruals import (
    AccrualDocument,
    AccrualDocumentType,
    AccrualStatus,
)
from app.domain.schemas.accruals import (
    AccrualDocumentCreate,
    AccrualDocumentResponse,
    CounterpartyBalanceResponse,
    DocumentClosureResponse,
    TransactionAccrualLinkRequest,
    TransactionAccrualLinkResponse,
    UnlinkResponse,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanViewReports,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.accrual_service import (
    get_counterparty_balances,
    get_document_closure,
    link_transaction_to_accrual,
    unlink_transaction_from_accrual,
)

router = APIRouter(tags=["Начисления и взаиморасчёты"])


# ─────────────────────────────────────────────────────────────────────────────
# CREATE DOCUMENT
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/accrual-documents",
    response_model=AccrualDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Провести документ начисления",
    description=(
        "Создаёт Акт оказания услуг (revenue), Реализацию или Накладную (expense). "
        "Документ сразу получает статус payment_status='unpaid'. "
        "Для закрытия долга используйте POST /accruals/link."
    ),
)
async def create_accrual_document(
    company_id:   UUID,
    body:         AccrualDocumentCreate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> AccrualDocumentResponse:
    doc = AccrualDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        counterparty_id=body.counterparty_id,
        doc_type=AccrualDocumentType(body.doc_type),
        doc_number=body.doc_number,
        doc_date=body.doc_date,
        amount=body.amount,
        payment_status=AccrualStatus.UNPAID,
        project_id=body.project_id,
        category_id=body.category_id,
        contract_id=body.contract_id,
        description=body.description,
    )
    db.add(doc)
    await db.flush()
    return AccrualDocumentResponse.model_validate(doc)


# ─────────────────────────────────────────────────────────────────────────────
# LIST DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/accrual-documents",
    response_model=list[AccrualDocumentResponse],
    summary="Реестр документов начислений",
    description=(
        "Возвращает список Актов/Накладных компании с фильтрами "
        "по типу, статусу оплаты и проекту."
    ),
)
async def list_accrual_documents(
    company_id:     UUID,
    doc_type:       Optional[str] = Query(None, description="revenue | expense"),
    payment_status: Optional[str] = Query(None, description="unpaid | partially_paid | paid | overpaid"),
    project_id:     Optional[UUID] = Query(None, description="Фильтр по проекту"),
    limit:          int  = Query(50, ge=1, le=200),
    offset:         int  = Query(0,  ge=0),
    current_user:   CurrentUser  = Depends(CanViewDashboard),
    db:             AsyncSession = Depends(get_db),
) -> list[AccrualDocumentResponse]:
    filters = [
        AccrualDocument.company_id == company_id,
        AccrualDocument.is_deleted.is_(False),
    ]
    if doc_type:
        try:
            filters.append(AccrualDocument.doc_type == AccrualDocumentType(doc_type))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неверный doc_type: {doc_type!r}. Допустимо: revenue, expense.",
            )
    if payment_status:
        try:
            filters.append(AccrualDocument.payment_status == AccrualStatus(payment_status))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неверный payment_status: {payment_status!r}. "
                       "Допустимо: unpaid, partially_paid, paid, overpaid.",
            )
    if project_id:
        filters.append(AccrualDocument.project_id == project_id)

    result = await db.execute(
        select(AccrualDocument)
        .where(and_(*filters))
        .order_by(AccrualDocument.doc_date.desc())
        .limit(limit)
        .offset(offset)
    )
    docs = result.scalars().all()
    return [AccrualDocumentResponse.model_validate(d) for d in docs]


# ─────────────────────────────────────────────────────────────────────────────
# GET DOCUMENT DETAIL (с историей платежей)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/accrual-documents/{doc_id}",
    response_model=DocumentClosureResponse,
    summary="Карточка документа + история закрывающих платежей",
)
async def get_accrual_document(
    company_id:   UUID,
    doc_id:       UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> DocumentClosureResponse:
    closure = await get_document_closure(db=db, accrual_id=doc_id)
    # IDOR: проверяем через _load_doc_or_404 внутри get_document_closure;
    # дополнительно убеждаемся что doc_id принадлежит этой компании
    doc_check = await db.execute(
        select(AccrualDocument.company_id).where(
            and_(AccrualDocument.id == doc_id, AccrualDocument.is_deleted.is_(False))
        )
    )
    doc_company = doc_check.scalar_one_or_none()
    if doc_company is None or doc_company != company_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Документ {doc_id} не найден в компании.",
        )
    return DocumentClosureResponse(
        accrual_id=closure.accrual_id,
        doc_number=closure.doc_number,
        doc_date=closure.doc_date,
        doc_type=closure.doc_type.value,
        total_amount=closure.total_amount,
        total_linked=closure.total_linked,
        remaining=closure.remaining,
        payment_status=closure.payment_status.value,
        links=closure.links,
    )


# ─────────────────────────────────────────────────────────────────────────────
# LINK — Привязать платёж к документу
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/accruals/link",
    response_model=TransactionAccrualLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Привязать платёж к документу начисления",
    description=(
        "Закрывает долг по Акту/Накладной на сумму linked_amount. "
        "Автоматически обновляет payment_status документа. "
        "Один платёж может частично закрывать несколько документов, "
        "один документ — закрываться несколькими платежами."
    ),
)
async def link_payment(
    company_id:   UUID,
    body:         TransactionAccrualLinkRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> TransactionAccrualLinkResponse:
    result = await link_transaction_to_accrual(
        db=db,
        transaction_id=body.transaction_id,
        accrual_document_id=body.accrual_document_id,
        amount=body.amount,
        user_id=current_user.user_id,
    )
    return TransactionAccrualLinkResponse(
        link_id=result.link_id,
        transaction_id=result.transaction_id,
        accrual_id=result.accrual_id,
        linked_amount=result.linked_amount,
        new_doc_status=result.new_doc_status.value,
        doc_remaining=result.doc_remaining,
        txn_remaining=result.txn_remaining,
    )


# ─────────────────────────────────────────────────────────────────────────────
# UNLINK — Снять привязку
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/accruals/link/{link_id}",
    response_model=UnlinkResponse,
    summary="Снять привязку платежа с документа",
    description=(
        "Удаляет TransactionAccrualLink и пересчитывает payment_status документа. "
        "Используется при ошибочной привязке."
    ),
)
async def unlink_payment(
    company_id:   UUID,
    link_id:      UUID,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> UnlinkResponse:
    result = await unlink_transaction_from_accrual(db=db, link_id=link_id)
    return UnlinkResponse(
        accrual_id=result.accrual_id,
        new_doc_status=result.new_doc_status.value,
        doc_remaining=result.doc_remaining,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTY BALANCES — Ведомость задолженности
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/counterparties/balances",
    response_model=list[CounterpartyBalanceResponse],
    summary="Ведомость дебиторской и кредиторской задолженности",
    description=(
        "Возвращает сальдо взаиморасчётов по каждому контрагенту. "
        "Дебиторка = непогашенные REVENUE-документы. "
        "Кредиторка = непогашенные EXPENSE-документы. "
        "Сортировка: по убыванию |net_position|. "
        "Параметр include_settled=true включает контрагентов с нулевым сальдо."
    ),
)
async def counterparty_balances(
    company_id:       UUID,
    include_settled:  bool = Query(False, description="Включать контрагентов с нулевым сальдо"),
    current_user:     CurrentUser  = Depends(CanViewReports),
    db:               AsyncSession = Depends(get_db),
) -> list[CounterpartyBalanceResponse]:
    balances = await get_counterparty_balances(
        db=db,
        company_id=company_id,
        include_settled=include_settled,
    )
    return [
        CounterpartyBalanceResponse(
            counterparty_id=b.counterparty_id,
            counterparty_name=b.counterparty_name,
            counterparty_inn=b.counterparty_inn,
            receivable_total=b.receivable_total,
            receivable_linked=b.receivable_linked,
            receivable_outstanding=b.receivable_outstanding,
            payable_total=b.payable_total,
            payable_linked=b.payable_linked,
            payable_outstanding=b.payable_outstanding,
            net_position=b.net_position,
        )
        for b in balances
    ]
