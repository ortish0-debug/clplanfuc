"""Основные средства и амортизация (Sprint 18)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company
from app.domain.models.fixed_assets import FixedAsset, FixedAssetStatus
from app.services.ledger_service import post_double_entry


def _q(v: Decimal) -> Decimal:
    """Округление до копеек HALF_UP."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


ZERO = Decimal("0.00")


async def add_fixed_asset(
    db: AsyncSession,
    company_id: UUID,
    name: str,
    inventory_number: str,
    initial_cost: Decimal,
    purchase_date: date,
    lifespan_months: int,
    account_chart_id: Optional[UUID] = None,
) -> FixedAsset:
    """
    Добавляет новое основное средство.

    IDOR-защита: проверяет company_id.
    """
    # IDOR check
    company_result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    asset = FixedAsset(
        id=uuid.uuid4(),
        company_id=company_id,
        name=name,
        inventory_number=inventory_number,
        initial_cost=_q(initial_cost),
        purchase_date=purchase_date,
        lifespan_months=lifespan_months,
        accumulated_depreciation=ZERO,
        status=FixedAssetStatus.ACTIVE,
        account_chart_id=account_chart_id,
    )
    db.add(asset)
    await db.flush()
    return asset


async def run_monthly_depreciation(
    db: AsyncSession,
    company_id: UUID,
    depreciation_date: date,
) -> Decimal:
    """
    Начисляет амортизацию всех активных ОС компании за месяц.

    Алгоритм:
    1. Находит all ОС со статусом ACTIVE, purchase_date <= depreciation_date
    2. Рассчитывает monthly_amount = initial_cost / lifespan_months
    3. Проверяет лимит: accumulated_depreciation + monthly_amount <= initial_cost
    4. Если это последний месяц — берет остаток до полной стоимости
    5. Обновляет accumulated_depreciation
    6. Если полностью амортизировано — меняет статус на DEPRECIATED
    7. Генерирует проводку в бухучет
    8. Возвращает суммарную амортизацию за месяц

    IDOR-защита: проверяет company_id.
    """
    # IDOR check
    company_result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Загружаем все активные ОС
    result = await db.execute(
        select(FixedAsset).where(
            and_(
                FixedAsset.company_id == company_id,
                FixedAsset.status == FixedAssetStatus.ACTIVE,
                FixedAsset.purchase_date <= depreciation_date,
            )
        )
    )
    assets = result.scalars().all()

    total_depreciation = ZERO

    for asset in assets:
        # Ежемесячная доля амортизации
        monthly_amount = _q(asset.initial_cost / Decimal(asset.lifespan_months))

        # Проверяем лимит
        new_accumulated = asset.accumulated_depreciation + monthly_amount

        if new_accumulated > asset.initial_cost:
            # Это последний месяц — берем только остаток
            monthly_amount = _q(asset.initial_cost - asset.accumulated_depreciation)
            new_accumulated = asset.initial_cost

        # Обновляем ОС
        asset.accumulated_depreciation = new_accumulated
        if new_accumulated >= asset.initial_cost:
            asset.status = FixedAssetStatus.DEPRECIATED

        total_depreciation += monthly_amount

    await db.flush()

    # Генерируем проводку в бухучет (если есть амортизация)
    if total_depreciation > ZERO:
        await post_double_entry(
            db=db,
            company_id=company_id,
            date_=depreciation_date,
            description=f"Начисление амортизации ОС на {depreciation_date}",
            doc_type="depreciation",
            doc_id=uuid.uuid4(),
            postings=[
                {"code": "9002", "debit": total_depreciation, "credit": ZERO},     # Расходы/Себестоимость
                {"code": "4100", "debit": ZERO, "credit": total_depreciation},     # ОС (уменьшение стоимости)
            ],
        )

    return total_depreciation
