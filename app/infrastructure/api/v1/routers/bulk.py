"""
FastAPI роутер: Массовые операции над транзакциями (Спринт 8).

Эндпоинты:
  POST /companies/{id}/transactions/bulk-update — перекатегоризация/смена проекта
  POST /companies/{id}/transactions/bulk-delete — мягкое удаление
  POST /companies/{id}/transactions/bulk-restore — восстановление удалённых

Все операции — единственный SQL UPDATE без N+1.
synchronize_session=False: объекты сессии не синхронизируются после UPDATE.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Category, Transaction

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.bulk_service import (
    BulkOperationResult,
    bulk_delete_transactions,
    bulk_restore_transactions,
    bulk_update_transactions,
    bulk_clear_project_id,
)

router = APIRouter(tags=["Массовые операции"])

_MAX_BULK = 500   # защита от слишком больших запросов


# ─────────────────────────────────────────────────────────────────────────────
# СХЕМЫ
# ─────────────────────────────────────────────────────────────────────────────


class BulkUpdateRequest(BaseModel):
    transaction_ids: list[UUID] = Field(
        ..., min_length=1, max_length=_MAX_BULK,
        description="Список UUID транзакций (макс. 500 за запрос)",
    )
    category_id: Optional[UUID] = Field(
        None,
        description="Новая категория. NULL = не менять.",
    )
    project_id: Optional[UUID] = Field(
        None,
        description="Новый проект. NULL = не менять. "
                    "Используйте bulk-clear-project для явного обнуления.",
    )


class BulkDeleteRequest(BaseModel):
    transaction_ids: list[UUID] = Field(
        ..., min_length=1, max_length=_MAX_BULK,
    )


class BulkResultResponse(BaseModel):
    requested: int
    updated:   int
    skipped:   int
    success:   bool
    message:   str


def _to_response(r: BulkOperationResult, verb: str = "обновлено") -> BulkResultResponse:
    msg = (
        f"{verb.capitalize()}: {r.updated} из {r.requested}. "
        f"Пропущено (чужие / уже удалены): {r.skipped}."
    )
    return BulkResultResponse(
        requested=r.requested,
        updated=r.updated,
        skipped=r.skipped,
        success=r.success,
        message=msg,
    )


# ─────────────────────────────────────────────────────────────────────────────
# BULK UPDATE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/transactions/bulk-update",
    response_model=BulkResultResponse,
    summary="Массовое обновление транзакций",
    description=(
        "Одним SQL-запросом обновляет `category_id` и/или `project_id` "
        "для списка транзакций. Транзакции других компаний игнорируются."
    ),
)
async def bulk_update(
    company_id:   UUID,
    body:         BulkUpdateRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BulkResultResponse:
    if body.category_id is None and body.project_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Необходимо указать хотя бы один параметр: category_id или project_id.",
        )
    result = await bulk_update_transactions(
        db=db,
        company_id=company_id,
        transaction_ids=body.transaction_ids,
        category_id=body.category_id,
        project_id=body.project_id,
    )
    return _to_response(result, "обновлено")


@router.post(
    "/companies/{company_id}/transactions/bulk-clear-project",
    response_model=BulkResultResponse,
    summary="Массовое снятие привязки к проекту",
    description="Устанавливает project_id = NULL для списка транзакций.",
)
async def bulk_clear_project(
    company_id:   UUID,
    body:         BulkDeleteRequest,   # те же поля — только transaction_ids
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BulkResultResponse:
    result = await bulk_clear_project_id(
        db=db,
        company_id=company_id,
        transaction_ids=body.transaction_ids,
    )
    return _to_response(result, "очищено")


# ─────────────────────────────────────────────────────────────────────────────
# BULK DELETE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/transactions/bulk-delete",
    response_model=BulkResultResponse,
    summary="Массовое мягкое удаление транзакций",
    description=(
        "Помечает транзакции как удалённые (is_deleted=True). "
        "Уже удалённые и чужие транзакции пропускаются."
    ),
)
async def bulk_delete(
    company_id:   UUID,
    body:         BulkDeleteRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BulkResultResponse:
    result = await bulk_delete_transactions(
        db=db,
        company_id=company_id,
        transaction_ids=body.transaction_ids,
    )
    return _to_response(result, "удалено")


# ─────────────────────────────────────────────────────────────────────────────
# BULK RESTORE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/transactions/bulk-restore",
    response_model=BulkResultResponse,
    summary="Массовое восстановление транзакций",
    description="Восстанавливает ранее удалённые транзакции.",
)
async def bulk_restore(
    company_id:   UUID,
    body:         BulkDeleteRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BulkResultResponse:
    result = await bulk_restore_transactions(
        db=db,
        company_id=company_id,
        transaction_ids=body.transaction_ids,
    )
    return _to_response(result, "восстановлено")


# ─────────────────────────────────────────────────────────────────────────────
# СПИСОК ТРАНЗАКЦИЙ (для таблицы с чекбоксами)
# ─────────────────────────────────────────────────────────────────────────────


class TransactionListItem(BaseModel):
    id:               UUID
    payment_date:     date
    description:      Optional[str]
    transaction_type: str
    amount:           DecimalStr
    currency:         str
    category_name:    Optional[str]
    project_id:       Optional[UUID]
    status:           str
    bank_transaction_id: Optional[str]


@router.get(
    "/companies/{company_id}/transactions",
    response_model=list[TransactionListItem],
    summary="Список транзакций компании",
    description="Последние транзакции для таблицы с чекбоксами и массовых операций.",
)
async def list_transactions(
    company_id:   UUID,
    limit:        int  = Query(50, ge=1, le=200),
    offset:       int  = Query(0,  ge=0),
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> list[TransactionListItem]:
    result = await db.execute(
        select(
            Transaction.id,
            Transaction.payment_date,
            Transaction.description,
            Transaction.transaction_type,
            Transaction.amount,
            Transaction.currency,
            Transaction.project_id,
            Transaction.status,
            Transaction.bank_transaction_id,
            Category.name.label("category_name"),
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
            )
        )
        .order_by(desc(Transaction.payment_date))
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()
    return [
        TransactionListItem(
            id=r.id,
            payment_date=r.payment_date,
            description=r.description,
            transaction_type=r.transaction_type.value if hasattr(r.transaction_type, "value") else r.transaction_type,
            amount=r.amount,
            currency=r.currency.value if hasattr(r.currency, "value") else r.currency,
            category_name=r.category_name,
            project_id=r.project_id,
            status=r.status.value if hasattr(r.status, "value") else r.status,
            bank_transaction_id=r.bank_transaction_id,
        )
        for r in rows
    ]
