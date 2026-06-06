"""
Вспомогательный сервис: создание транзакций из других модулей.
Используется в: payroll, loans, assets — чтобы каждое финансовое событие
автоматически отражалось в ДДС и попадало в отчёты.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account, Category, CategoryType, Transaction,
    TransactionStatus, TransactionType, Currency,
)


async def _get_first_account(db: AsyncSession, company_id: UUID) -> Optional[Account]:
    """Возвращает первый активный счёт компании."""
    result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted == False,
                Account.is_active == True,
            )
        ).order_by(Account.created_at)
    )
    return result.scalars().first()


async def _get_or_create_category(
    db: AsyncSession,
    company_id: UUID,
    name: str,
    cat_type: CategoryType,
) -> Category:
    """Находит или создаёт системную категорию по имени."""
    result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id == company_id,
                Category.name == name,
                Category.is_deleted == False,
            )
        )
    )
    cat = result.scalar_one_or_none()
    if cat:
        return cat
    cat = Category(
        id=uuid.uuid4(),
        company_id=company_id,
        name=name,
        category_type=cat_type,
        is_system=True,
        is_deleted=False,
    )
    db.add(cat)
    await db.flush()
    return cat


async def create_expense_transaction(
    db: AsyncSession,
    company_id: UUID,
    amount: Decimal,
    description: str,
    payment_date: date,
    category_name: str,
    account_id: Optional[UUID] = None,
) -> Transaction:
    """
    Создаёт транзакцию EXPENSE и обновляет баланс счёта.
    Если account_id не передан — берёт первый счёт компании.
    """
    # Получаем счёт
    if account_id:
        acc_result = await db.execute(
            select(Account).where(
                and_(Account.id == account_id, Account.company_id == company_id)
            )
        )
        account = acc_result.scalar_one_or_none()
    else:
        account = None

    if not account:
        account = await _get_first_account(db, company_id)

    if not account:
        raise ValueError(f"У компании {company_id} нет ни одного счёта")

    # Получаем/создаём категорию
    category = await _get_or_create_category(
        db, company_id, category_name, CategoryType.EXPENSE
    )

    # Создаём транзакцию
    tx = Transaction(
        id=uuid.uuid4(),
        company_id=company_id,
        account_id=account.id,
        category_id=category.id,
        transaction_type=TransactionType.EXPENSE,
        amount=amount,
        amount_base_currency=amount,
        currency=account.currency,
        exchange_rate=Decimal("1.0"),
        description=description,
        payment_date=payment_date,
        status=TransactionStatus.CONFIRMED,
        tags=[],
        meta={},
        ai_classified=False,
        is_deleted=False,
        is_intra_group=False,
    )

    # Обновляем баланс счёта
    account.current_balance -= amount

    db.add(tx)
    await db.flush()
    return tx


async def create_income_transaction(
    db: AsyncSession,
    company_id: UUID,
    amount: Decimal,
    description: str,
    payment_date: date,
    category_name: str,
    account_id: Optional[UUID] = None,
) -> Transaction:
    """Создаёт транзакцию INCOME и обновляет баланс счёта."""
    if account_id:
        acc_result = await db.execute(
            select(Account).where(
                and_(Account.id == account_id, Account.company_id == company_id)
            )
        )
        account = acc_result.scalar_one_or_none()
    else:
        account = None

    if not account:
        account = await _get_first_account(db, company_id)

    if not account:
        raise ValueError(f"У компании {company_id} нет ни одного счёта")

    category = await _get_or_create_category(
        db, company_id, category_name, CategoryType.INCOME
    )

    tx = Transaction(
        id=uuid.uuid4(),
        company_id=company_id,
        account_id=account.id,
        category_id=category.id,
        transaction_type=TransactionType.INCOME,
        amount=amount,
        amount_base_currency=amount,
        currency=account.currency,
        exchange_rate=Decimal("1.0"),
        description=description,
        payment_date=payment_date,
        status=TransactionStatus.CONFIRMED,
        tags=[],
        meta={},
        ai_classified=False,
        is_deleted=False,
        is_intra_group=False,
    )

    account.current_balance += amount

    db.add(tx)
    await db.flush()
    return tx
