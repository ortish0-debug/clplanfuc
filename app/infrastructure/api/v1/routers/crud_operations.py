"""
CRUD операции для счетов, категорий и транзакций.

Эндпоинты:
  GET    /companies/{company_id}/accounts              — список счетов
  POST   /companies/{company_id}/accounts              — создать счет
  DELETE /companies/{company_id}/accounts/{account_id} — удалить счет
  GET    /companies/{company_id}/categories            — список категорий
  POST   /companies/{company_id}/categories            — создать категорию
  DELETE /companies/{company_id}/categories/{category_id} — удалить категорию
  POST   /companies/{company_id}/transactions          — создать транзакцию
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Category, Transaction, AccountType, CategoryType, Currency, TransactionType, TransactionStatus
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db

router = APIRouter(tags=["CRUD Operations"])


# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────────────────────────────────────


class AccountCreate(BaseModel):
    name: str
    account_type: str  # CHECKING, SAVINGS, CASH, CREDIT, INVESTMENT, BANK
    currency: str = "RUB"          # Default to RUB
    currency_code: Optional[str] = None  # alias из фронтенда
    balance: Optional[float] = None      # начальный баланс из фронтенда
    initial_balance: Optional[float] = None


class AccountResponse(BaseModel):
    id: UUID
    name: str
    account_type: str
    currency: str
    current_balance: str
    is_active: bool


class CategoryCreate(BaseModel):
    name: str
    category_type: str  # INCOME, EXPENSE
    parent_id: Optional[UUID] = None
    color: Optional[str] = None


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    category_type: str
    parent_id: Optional[UUID] = None
    color: Optional[str] = None


class TransactionCreate(BaseModel):
    account_id: UUID
    category_id: Optional[UUID] = None
    amount: float
    transaction_type: str  # INCOME, EXPENSE, TRANSFER
    description: Optional[str] = ""
    payment_date: Optional[str] = None   # YYYY-MM-DD; если нет — сегодня
    counterparty_id: Optional[UUID] = None  # опционально из фронтенда


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    transaction_type: Optional[str] = None
    payment_date: Optional[str] = None
    category_id: Optional[UUID] = None


class TransactionResponse(BaseModel):
    id: UUID
    account_id: UUID
    category_id: Optional[UUID] = None
    amount: str
    transaction_type: str
    description: str
    payment_date: str


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/accounts",
    response_model=dict,
    summary="Список счетов",
)
async def list_accounts(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Получить все активные счета компании."""
    result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted.is_(False)
            )
        ).order_by(Account.sort_order)
    )
    accounts = result.scalars().all()
    return {
        "accounts": [
            {
                "id": str(acc.id),
                "name": acc.name,
                "account_type": acc.account_type.value,
                "currency": acc.currency.value,
                "current_balance": str(acc.current_balance),
                "is_active": acc.is_active,
            }
            for acc in accounts
        ]
    }


@router.post(
    "/companies/{company_id}/accounts",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Создать счет",
)
async def create_account(
    company_id: UUID,
    body: AccountCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Создать новый счет."""
    # Поддержка currency_code как алиас currency (из фронтенда)
    currency_str = (body.currency_code or body.currency or "RUB").upper()
    # Нормализация account_type: BANK → CHECKING
    account_type_str = body.account_type.upper()
    if account_type_str == "BANK":
        account_type_str = "CHECKING"

    try:
        account_type = AccountType[account_type_str]
        currency = Currency[currency_str]
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неверный тип счета или валюта",
        )

    # Начальный баланс: принимаем balance или initial_balance
    start_balance = Decimal(str(body.balance or body.initial_balance or 0))

    account = Account(
        company_id=company_id,
        name=body.name,
        account_type=account_type,
        currency=currency,
        initial_balance=start_balance,
        current_balance=start_balance,
        is_active=True,
    )
    db.add(account)
    await db.flush()
    await db.commit()

    return {"id": str(account.id), "status": "created"}


@router.delete(
    "/companies/{company_id}/accounts/{account_id}",
    response_model=dict,
    summary="Удалить счет",
)
async def delete_account(
    company_id: UUID,
    account_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Мягко удалить счет."""
    result = await db.execute(
        select(Account).where(
            and_(
                Account.id == account_id,
                Account.company_id == company_id,
                Account.is_deleted.is_(False)
            )
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Счет не найден")

    account.is_deleted = True
    await db.commit()

    return {"status": "deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/categories",
    response_model=dict,
    summary="Список категорий",
)
async def list_categories(
    company_id: UUID,
    category_type: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Получить все категории компании."""
    filters = [
        Category.company_id == company_id,
        Category.is_deleted.is_(False)
    ]
    if category_type:
        try:
            cat_type = CategoryType[category_type.upper()]
            filters.append(Category.category_type == cat_type)
        except KeyError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Неверный тип категории",
            )

    result = await db.execute(
        select(Category).where(and_(*filters)).order_by(Category.sort_order)
    )
    categories = result.scalars().all()
    return {
        "categories": [
            {
                "id": str(cat.id),
                "name": cat.name,
                "category_type": cat.category_type.value,
                "parent_id": str(cat.parent_id) if cat.parent_id else None,
                "color": cat.color,
            }
            for cat in categories
        ]
    }


@router.post(
    "/companies/{company_id}/categories",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Создать категорию",
)
async def create_category(
    company_id: UUID,
    body: CategoryCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Создать новую категорию."""
    try:
        cat_type = CategoryType[body.category_type.upper()]
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неверный тип категории",
        )

    category = Category(
        company_id=company_id,
        name=body.name,
        category_type=cat_type,
        parent_id=body.parent_id,
        color=body.color,
    )
    db.add(category)
    await db.flush()
    await db.commit()

    return {"id": str(category.id), "status": "created"}


@router.delete(
    "/companies/{company_id}/categories/{category_id}",
    response_model=dict,
    summary="Удалить категорию",
)
async def delete_category(
    company_id: UUID,
    category_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Мягко удалить категорию (только если не системная)."""
    result = await db.execute(
        select(Category).where(
            and_(
                Category.id == category_id,
                Category.company_id == company_id,
                Category.is_deleted.is_(False)
            )
        )
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Категория не найдена")

    if category.is_system:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить системную категорию"
        )

    category.is_deleted = True
    await db.commit()

    return {"status": "deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTIONS
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/transactions",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Создать транзакцию",
)
async def create_transaction(
    company_id: UUID,
    body: TransactionCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Создать новую транзакцию и обновить баланс счета."""
    # Validate account exists
    acc_result = await db.execute(
        select(Account).where(
            and_(
                Account.id == body.account_id,
                Account.company_id == company_id,
                Account.is_deleted.is_(False)
            )
        )
    )
    account = acc_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Счет не найден")

    # Validate transaction type
    try:
        tx_type = TransactionType[body.transaction_type.upper()]
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неверный тип транзакции",
        )

    # Create transaction
    amount = Decimal(str(body.amount))
    from datetime import date as _date
    if body.payment_date:
        payment_date = _date.fromisoformat(body.payment_date[:10])
    else:
        payment_date = _date.today()

    transaction = Transaction(
        company_id=company_id,
        account_id=body.account_id,
        category_id=body.category_id,
        transaction_type=tx_type,
        amount=amount,
        amount_base_currency=amount,
        currency=account.currency,
        exchange_rate=Decimal("1.0"),
        description=body.description,
        payment_date=payment_date,
        status=TransactionStatus.CONFIRMED,
        tags=[],
        meta={},
        ai_classified=False,
        is_deleted=False,
        is_intra_group=False,
    )

    # Update account balance
    if tx_type == TransactionType.INCOME:
        account.current_balance += amount
    elif tx_type == TransactionType.EXPENSE:
        account.current_balance -= amount

    db.add(transaction)
    await db.flush()
    await db.commit()

    return {"id": str(transaction.id), "status": "created"}


@router.patch(
    "/companies/{company_id}/transactions/{transaction_id}",
    response_model=dict,
    summary="Редактировать транзакцию",
)
async def update_transaction(
    company_id: UUID,
    transaction_id: UUID,
    body: TransactionUpdate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить транзакцию с пересчётом баланса счёта."""
    result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.id == transaction_id,
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Транзакция не найдена")

    acc_result = await db.execute(
        select(Account).where(Account.id == transaction.account_id)
    )
    account = acc_result.scalar_one_or_none()

    # Откатываем старый баланс
    if account:
        if transaction.transaction_type == TransactionType.INCOME:
            account.current_balance -= transaction.amount
        elif transaction.transaction_type == TransactionType.EXPENSE:
            account.current_balance += transaction.amount

    # Применяем изменения
    if body.description is not None:
        transaction.description = body.description
    if body.amount is not None:
        transaction.amount = Decimal(str(body.amount))
        transaction.amount_base_currency = Decimal(str(body.amount))
    if body.transaction_type is not None:
        try:
            transaction.transaction_type = TransactionType[body.transaction_type.upper()]
        except KeyError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неверный тип транзакции")
    if body.payment_date is not None:
        transaction.payment_date = date.fromisoformat(body.payment_date)
    if body.category_id is not None:
        transaction.category_id = body.category_id

    # Применяем новый баланс
    if account:
        if transaction.transaction_type == TransactionType.INCOME:
            account.current_balance += transaction.amount
        elif transaction.transaction_type == TransactionType.EXPENSE:
            account.current_balance -= transaction.amount

    await db.commit()
    return {"status": "updated", "id": str(transaction_id)}


@router.delete(
    "/companies/{company_id}/transactions/{transaction_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Удалить транзакцию",
)
async def delete_transaction(
    company_id: UUID,
    transaction_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Мягкое удаление транзакции + откат баланса счёта."""
    result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.id == transaction_id,
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Транзакция не найдена")

    # Откатываем баланс счёта
    acc_result = await db.execute(
        select(Account).where(Account.id == transaction.account_id)
    )
    account = acc_result.scalar_one_or_none()
    if account:
        if transaction.transaction_type == TransactionType.INCOME:
            account.current_balance -= transaction.amount
        elif transaction.transaction_type == TransactionType.EXPENSE:
            account.current_balance += transaction.amount

    transaction.is_deleted = True
    await db.commit()

    return {"status": "deleted", "id": str(transaction_id)}
