"""
FastAPI роутер: Заявки на оплату (PaymentRequest).

Эндпоинты:
  GET    /companies/{id}/payment-requests                — список (фильтр по status)
  POST   /companies/{id}/payment-requests                — создать заявку
  GET    /companies/{id}/payment-requests/{req_id}       — одна заявка
  PATCH  /companies/{id}/payment-requests/{req_id}/status— изменить статус
  DELETE /companies/{id}/payment-requests/{req_id}       — удалить черновик

Жизненный цикл:
  PENDING → (approve) → APPROVED → (pay, создаётся Transaction) → PAID
  PENDING → (reject)  → REJECTED
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.projects import PaymentRequest, PaymentRequestStatus
from app.domain.schemas.payment_requests import (
    PaymentRequestCreate,
    PaymentRequestResponse,
    PaymentRequestStatusUpdate,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanWriteFinance,
    CanWriteOperational,
    CurrentUser,
)
from app.infrastructure.database.session import get_db

router = APIRouter(tags=["Заявки на оплату"])


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_or_404(
    db: AsyncSession, req_id: UUID, company_id: UUID
) -> PaymentRequest:
    r = await db.execute(
        select(PaymentRequest).where(
            and_(
                PaymentRequest.id         == req_id,
                PaymentRequest.company_id == company_id,
            )
        )
    )
    req = r.scalar_one_or_none()
    if not req:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Заявка {req_id} не найдена.")
    return req


def _to_resp(req: PaymentRequest) -> PaymentRequestResponse:
    return PaymentRequestResponse(
        id=req.id,
        company_id=req.company_id,
        category_id=req.category_id,
        project_id=req.project_id,
        applicant_user_id=req.applicant_user_id,
        reviewer_user_id=req.reviewer_user_id,
        amount=req.amount,
        planned_date=req.planned_date,
        status=req.status.value,
        transaction_type=req.transaction_type or "EXPENSE",
        description=req.description,
        rejection_reason=req.rejection_reason,
        reviewed_at=req.reviewed_at,
        created_at=req.created_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/payment-requests",
    response_model=list[PaymentRequestResponse],
    summary="Список заявок на оплату",
)
async def list_requests(
    company_id:   UUID,
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="pending | approved | rejected | paid"
    ),
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> list[PaymentRequestResponse]:
    filters = [PaymentRequest.company_id == company_id]
    if status_filter:
        try:
            filters.append(PaymentRequest.status == PaymentRequestStatus(status_filter))
        except ValueError:
            pass

    result = await db.execute(
        select(PaymentRequest)
        .where(and_(*filters))
        .order_by(PaymentRequest.planned_date)
    )
    return [_to_resp(r) for r in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# GET ONE
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/payment-requests/{req_id}",
    response_model=PaymentRequestResponse,
    summary="Получить заявку по ID",
)
async def get_request(
    company_id:   UUID,
    req_id:       UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> PaymentRequestResponse:
    return _to_resp(await _get_or_404(db, req_id, company_id))


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/payment-requests",
    response_model=PaymentRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заявку на оплату",
)
async def create_request(
    company_id:   UUID,
    body:         PaymentRequestCreate,
    current_user: CurrentUser  = Depends(CanWriteOperational),  # OWNER, ADMIN, ACCOUNTANT, MANAGER
    db:           AsyncSession = Depends(get_db),
) -> PaymentRequestResponse:
    req = PaymentRequest(
        id=uuid.uuid4(),
        company_id=company_id,
        category_id=body.category_id,
        project_id=body.project_id,
        applicant_user_id=current_user.user_id,
        amount=body.amount,
        planned_date=body.planned_date,
        status=PaymentRequestStatus.PENDING,
        description=body.description,
        transaction_type=(body.transaction_type or "EXPENSE").upper(),
    )
    db.add(req)
    await db.flush()
    return _to_resp(req)


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE STATUS  (approve / reject)
# ─────────────────────────────────────────────────────────────────────────────


@router.patch(
    "/companies/{company_id}/payment-requests/{req_id}/status",
    response_model=PaymentRequestResponse,
    summary="Изменить статус заявки (одобрить / отклонить)",
)
async def update_status(
    company_id:   UUID,
    req_id:       UUID,
    body:         PaymentRequestStatusUpdate,
    current_user: CurrentUser  = Depends(CanWriteFinance),  # только фин. роли
    db:           AsyncSession = Depends(get_db),
) -> PaymentRequestResponse:
    req = await _get_or_404(db, req_id, company_id)

    if req.status != PaymentRequestStatus.PENDING:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Нельзя изменить статус заявки со статусом '{req.status.value}'.",
        )

    new_status = PaymentRequestStatus(body.status)

    if new_status == PaymentRequestStatus.REJECTED and not body.rejection_reason:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="При отклонении заявки необходимо указать rejection_reason.",
        )

    req.status           = new_status
    req.reviewer_user_id = current_user.user_id
    req.reviewed_at      = datetime.now(tz=timezone.utc)
    if body.rejection_reason:
        req.rejection_reason = body.rejection_reason

    await db.flush()
    return _to_resp(req)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE  (только черновики PENDING своего автора или финансовые роли)
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/payment-requests/{req_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить заявку (только в статусе PENDING)",
)
async def delete_request(
    company_id:   UUID,
    req_id:       UUID,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> None:
    req = await _get_or_404(db, req_id, company_id)
    if req.status != PaymentRequestStatus.PENDING:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Нельзя удалить заявку со статусом '{req.status.value}'.",
        )
    await db.delete(req)
    await db.flush()
