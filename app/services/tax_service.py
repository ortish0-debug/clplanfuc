"""Налоговый контур: расчёты НДС и регистрация (Sprint 17)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company
from app.domain.models.taxes import VatRate, VatRecord


def _q(v: Decimal) -> Decimal:
    """Округление до копеек HALF_UP."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rate_to_decimal(rate: VatRate) -> Decimal:
    """Преобразование ставки НДС в десятичный коэффициент."""
    rates = {
        VatRate.VAT_0: Decimal("0.00"),
        VatRate.VAT_10: Decimal("0.10"),
        VatRate.VAT_20: Decimal("0.20"),
        VatRate.VAT_NONE: Decimal("0.00"),
    }
    return rates[rate]


def calculate_vat(
    total_amount: Decimal,
    rate: VatRate,
    price_includes_vat: bool = True,
) -> tuple[Decimal, Decimal]:
    """
    Расчёт НДС.

    Args:
        total_amount: Сумма (либо с НДС, либо без)
        rate: Ставка НДС
        price_includes_vat: True → сумма с НДС внутри, False → сумма без НДС

    Returns:
        (base_amount, vat_amount) оба в копейках (HALF_UP)

    Примеры:
        - price_includes_vat=True:  Сумма 100 (с НДС 20%) → база 83.33, НДС 16.67
        - price_includes_vat=False: Сумма 100 (без НДС 20%) → база 100, НДС 20
    """
    rate_dec = _rate_to_decimal(rate)

    if price_includes_vat:
        # НДС внутри суммы: вычисляем НДС методом выделения
        # vat = total * rate / (1 + rate)
        divisor = Decimal("1") + rate_dec
        if divisor == Decimal("0"):
            vat_amount = Decimal("0.00")
        else:
            vat_amount = _q(total_amount * rate_dec / divisor)
        base_amount = _q(total_amount - vat_amount)
    else:
        # НДС сверху суммы
        base_amount = _q(total_amount)
        vat_amount = _q(total_amount * rate_dec)

    return base_amount, vat_amount


async def record_vat_transaction(
    db: AsyncSession,
    company_id: UUID,
    doc_type: str,
    doc_id: UUID,
    rate: VatRate,
    total_amount: Decimal,
    is_input: bool,
    operation_date: date,
    price_includes_vat: bool = True,
) -> VatRecord:
    """
    Регистрирует транзакцию НДС в реестр tax_vat_records.

    IDOR-защита: проверяет что company_id существует.

    Args:
        db: AsyncSession
        company_id: UUID компании (IDOR)
        doc_type: Тип первичного документа ('invoice', 'accrual', 'expense', etc.)
        doc_id: UUID документа-источника
        rate: VatRate (vat_0, vat_10, vat_20, vat_none)
        total_amount: Сумма по документу
        is_input: True = входящий НДС (к вычету), False = исходящий (к уплате)
        operation_date: Дата операции
        price_includes_vat: Сумма включает НДС или нет

    Returns:
        VatRecord (сохранённая в БД)

    Raises:
        HTTP 404: Компания не найдена
    """
    # IDOR check: компания существует
    company_result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Расчёт НДС
    base_amount, vat_amount = calculate_vat(total_amount, rate, price_includes_vat)

    # Пересчитываем итоговую сумму (может отличаться от входящей из-за округления)
    calc_total_amount = _q(base_amount + vat_amount)

    # Создаём запись
    record = VatRecord(
        id=uuid.uuid4(),
        company_id=company_id,
        document_type=doc_type,
        document_id=doc_id,
        vat_rate=rate,
        base_amount=base_amount,
        vat_amount=vat_amount,
        total_amount=calc_total_amount,
        is_input=is_input,
        operation_date=operation_date,
    )
    db.add(record)
    await db.flush()

    return record
