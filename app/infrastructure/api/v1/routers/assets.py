"""
FastAPI роутер: Основные средства (Спринт 7).

Эндпоинты:
  POST  /companies/{id}/assets             — поставить ОС на учёт
  GET   /companies/{id}/assets             — список ОС с вычисленной остаточной стоимостью
  GET   /companies/{id}/assets/{asset_id}  — карточка ОС
  PATCH /companies/{id}/assets/{asset_id}  — обновить параметры ОС
  DELETE /companies/{id}/assets/{asset_id} — снять с учёта (мягкое удаление)
  POST  /companies/{id}/assets/amortize    — ручной триггер начисления амортизации
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.assets_loans import Asset
from app.domain.schemas.assets_loans import (
    AmortizationResultResponse,
    AmortizeRequest,
    AssetCreate,
    AssetResponse,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.asset_service import (
    AmortizationResult,
    get_asset_summary,
    run_monthly_amortization,
)
from app.services.transaction_factory import create_expense_transaction

router = APIRouter(tags=["Основные средства"])


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_asset_or_404(
    db: AsyncSession,
    asset_id: UUID,
    company_id: UUID,
) -> Asset:
    result = await db.execute(
        select(Asset).where(
            and_(
                Asset.id         == asset_id,
                Asset.company_id == company_id,
                Asset.is_deleted.is_(False),
            )
        )
    )
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"ОС {asset_id} не найдено.",
        )
    return a


def _asset_to_response(a: Asset) -> AssetResponse:
    return AssetResponse(
        id=a.id,
        company_id=a.company_id,
        name=a.name,
        description=a.description,
        purchase_cost=a.purchase_cost,
        purchase_date=a.purchase_date,
        amortization_months=a.amortization_months,
        accumulated_amortization=a.accumulated_amortization,
        account_id=a.account_id,
        is_active=a.is_active,
        monthly_amortization=a.monthly_amortization,
        residual_value=a.residual_value,
        is_fully_amortized=a.is_fully_amortized,
    )


def _amort_to_response(r: AmortizationResult) -> AmortizationResultResponse:
    return AmortizationResultResponse(
        asset_id=r.asset_id,
        asset_name=r.asset_name,
        month_label=r.month_label,
        amount=r.amount,
        accumulated=r.accumulated,
        residual_value=r.residual_value,
        is_fully_amortized=r.is_fully_amortized,
        transaction_id=r.transaction_id,
        skipped=r.skipped,
    )


# ─────────────────────────────────────────────────────────────────────────────
# СПИСОК
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/assets",
    response_model=list[AssetResponse],
    summary="Список основных средств",
    description=(
        "Возвращает все ОС компании со вычисленной остаточной стоимостью, "
        "суммой ежемесячной амортизации и признаком полной амортизации."
    ),
)
async def list_assets(
    company_id:   UUID,
    is_active:    Optional[bool] = Query(None, description="Фильтр: только активные / все"),
    current_user: CurrentUser    = Depends(CanViewDashboard),
    db:           AsyncSession   = Depends(get_db),
) -> list[AssetResponse]:
    filters = [
        Asset.company_id == company_id,
        Asset.is_deleted.is_(False),
    ]
    if is_active is not None:
        filters.append(Asset.is_active.is_(is_active))

    result = await db.execute(
        select(Asset).where(and_(*filters)).order_by(Asset.name)
    )
    return [_asset_to_response(a) for a in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# КАРТОЧКА
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/assets/{asset_id}",
    response_model=AssetResponse,
    summary="Карточка основного средства",
)
async def get_asset(
    company_id:   UUID,
    asset_id:     UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> AssetResponse:
    return _asset_to_response(
        await _get_asset_or_404(db, asset_id, company_id)
    )


# ─────────────────────────────────────────────────────────────────────────────
# СОЗДАНИЕ
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Поставить ОС на учёт",
    description="Создаёт карточку основного средства. Амортизация начисляется ежемесячно через /assets/amortize.",
)
async def create_asset(
    company_id:   UUID,
    body:         AssetCreate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> AssetResponse:
    from app.domain.models.finance import Decimal
    from decimal import Decimal as D

    a = Asset(
        id=uuid.uuid4(),
        company_id=company_id,
        name=body.name,
        description=body.description,
        purchase_cost=body.purchase_cost,
        purchase_date=body.purchase_date,
        amortization_months=body.amortization_months,
        accumulated_amortization=D("0.00"),
        account_id=body.account_id,
        is_active=True,
        is_deleted=False,
    )
    db.add(a)
    await db.flush()

    # Транзакция расхода — покупка актива
    await create_expense_transaction(
        db=db,
        company_id=company_id,
        amount=body.purchase_cost,
        description=f"Покупка ОС: {body.name}",
        payment_date=body.purchase_date,
        category_name="Основные средства",
        account_id=body.account_id,
    )

    return _asset_to_response(a)


# ─────────────────────────────────────────────────────────────────────────────
# ОБНОВЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


@router.patch(
    "/companies/{company_id}/assets/{asset_id}",
    response_model=AssetResponse,
    summary="Обновить параметры ОС",
    description="Позволяет скорректировать описание, срок СПИ или статус. Не изменяет историю начисления.",
)
async def update_asset(
    company_id:   UUID,
    asset_id:     UUID,
    body:         dict,       # частичное обновление — произвольные поля
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> AssetResponse:
    a = await _get_asset_or_404(db, asset_id, company_id)

    # Разрешённые для редактирования поля
    _editable = {"name", "description", "amortization_months", "is_active"}
    for field, value in body.items():
        if field in _editable and value is not None:
            setattr(a, field, value)

    await db.flush()
    return _asset_to_response(a)


# ─────────────────────────────────────────────────────────────────────────────
# МЯГКОЕ УДАЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Снять ОС с учёта",
    description="Мягкое удаление: is_deleted=True, is_active=False. История транзакций сохраняется.",
)
async def delete_asset(
    company_id:   UUID,
    asset_id:     UUID,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> None:
    a = await _get_asset_or_404(db, asset_id, company_id)
    a.is_deleted = True
    a.is_active  = False
    a.deleted_at = datetime.now(tz=timezone.utc)
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# НАЧИСЛЕНИЕ АМОРТИЗАЦИИ (ручной триггер)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/assets/amortize",
    response_model=list[AmortizationResultResponse],
    status_code=status.HTTP_200_OK,
    summary="Начислить ежемесячную амортизацию",
    description=(
        "Ручной триггер: начисляет амортизацию для всех активных ОС за указанную дату. "
        "Идемпотентен: повторный вызов за тот же месяц пропускает уже обработанные ОС. "
        "В продакшене вызывается планировщиком 1-го числа каждого месяца."
    ),
)
async def trigger_amortization(
    company_id:   UUID,
    body:         AmortizeRequest,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> list[AmortizationResultResponse]:
    results = await run_monthly_amortization(
        db=db,
        company_id=company_id,
        current_date=body.amortization_date,
    )

    # Транзакция расхода на каждое начисление амортизации
    from decimal import Decimal as D
    for r in results:
        if r.amount_charged and r.amount_charged > 0:
            await create_expense_transaction(
                db=db,
                company_id=company_id,
                amount=D(str(r.amount_charged)),
                description=f"Амортизация: {r.asset_name}",
                payment_date=body.amortization_date,
                category_name="Амортизация",
            )

    return [_amort_to_response(r) for r in results]
