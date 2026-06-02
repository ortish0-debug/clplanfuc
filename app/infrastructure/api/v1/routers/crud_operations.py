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
    account_type: str  # CHECKING, SAVINGS, CASH, CREDIT, INVESTMENT
    currency: str = "RUB"  # Default to RUB


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
    description: str
    payment_date: str  # YYYY-MM-DD


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
    try:
        account_type = AccountType[body.account_type.upper()]
        currency = Currency[body.currency.upper()]
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Неверный тип счета или валюта",
        )

    account = Account(
        company_id=company_id,
        name=body.name,
        account_type=account_type,
        currency=currency,
        initial_balance=Decimal("0.00"),
        current_balance=Decimal("0.00"),
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
    payment_date = date.fromisoformat(body.payment_date) if isinstance(body.payment_date, str) else body.payment_date

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
