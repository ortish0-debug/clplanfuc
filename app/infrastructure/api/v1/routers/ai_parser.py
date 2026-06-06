"""
FastAPI роутер: AI-парсинг транзакций.
POST /api/v1/companies/{company_id}/transactions/parse-raw

Алгоритм обработки запроса:
  1. RBAC: только OWNER / ADMIN / ACCOUNTANT
  2. Проверить, что account_id принадлежит company_id
  3. Загрузить категории компании для контекста Gemini
  4. Вызвать GeminiParser.classify_raw_text()
  5. Создать запись Transaction (status=confirmed, ai_classified=True)
  6. Вернуть AIParseResponseSchema
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account,
    Category,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domain.schemas.ai_parser import (
    AIParseResponseSchema,
    ParseRawTransactionRequest,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.infrastructure.integrations.ai.gemini_parser import (
    GeminiParser,
    get_gemini_parser,
)

router = APIRouter(
    prefix="/companies/{company_id}/transactions",
    tags=["AI-парсинг транзакций"],
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _map_transaction_type(raw: str) -> TransactionType:
    mapping = {
        "income": TransactionType.INCOME,
        "expense": TransactionType.EXPENSE,
        "transfer": TransactionType.TRANSFER,
    }
    return mapping.get(raw.lower(), TransactionType.EXPENSE)


async def _load_account(
    db: AsyncSession,
    account_id: UUID,
    company_id: UUID,
) -> Account:
    """
    Загружает счёт и проверяет, что он принадлежит указанной компании.
    Защита от IDOR-атаки: пользователь не должен указывать чужой account_id.
    """
    result = await db.execute(
        select(Account).where(
            and_(
                Account.id == account_id,
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
    )
    account: Optional[Account] = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Счёт {account_id} не найден или не принадлежит компании {company_id}."
            ),
        )
    return account


async def _load_categories(
    db: AsyncSession,
    company_id: UUID,
) -> list[dict]:
    """
    Загружает активные категории компании в формате словарей для Gemini-промпта.
    Поле 'icon' используется как slug категории.
    """
    result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id == company_id,
                Category.is_deleted.is_(False),
            )
        ).order_by(Category.sort_order)
    )
    categories = result.scalars().all()
    return [
        {
            "id": str(cat.id),
            "name": cat.name,
            "slug": cat.icon or cat.name.lower().replace(" ", "_"),
            "icon": cat.icon,
            "category_type": cat.category_type.value,
        }
        for cat in categories
    ]


async def _find_category_id(
    db: AsyncSession,
    company_id: UUID,
    category_slug: str,
) -> Optional[UUID]:
    """Ищет категорию по slug (поле icon). Возвращает None если не найдена."""
    if not category_slug or category_slug == "other":
        return None

    result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id == company_id,
                Category.icon == category_slug,
                Category.is_deleted.is_(False),
            )
        ).limit(1)
    )
    cat: Optional[Category] = result.scalar_one_or_none()
    return cat.id if cat else None


def _parse_amount_safe(raw: str) -> Decimal:
    """Конвертирует строку в Decimal. При любой ошибке возвращает 0.00."""
    try:
        return Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return Decimal("0.00")


# ---------------------------------------------------------------------------
# Обновление кэшированного баланса счёта (денормализованное поле)
# ---------------------------------------------------------------------------


def _update_account_balance(account: Account, amount: Decimal, txn_type: TransactionType) -> None:
    """Корректирует account.current_balance после создания транзакции."""
    if txn_type == TransactionType.INCOME:
        account.current_balance += amount
    elif txn_type == TransactionType.EXPENSE:
        account.current_balance -= amount
    # TRANSFER: изменение баланса обрабатывается отдельно при наличии destination_account


# ---------------------------------------------------------------------------
# Эндпоинт
# ---------------------------------------------------------------------------


@router.post(
    "/parse-raw",
    response_model=AIParseResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="AI-парсинг сырого текста → создание транзакции",
    description=(
        "Принимает произвольный текст (описание операции, выписка банка, фото чека "
        "в текстовом виде). Gemini 2.5 Flash классифицирует операцию и автоматически "
        "создаёт подтверждённую транзакцию. "
        "При недоступности Gemini используется regex-парсер (is_fallback=true). "
        "Доступно: Owner, Admin, Accountant."
    ),
)
async def parse_raw_transaction(
    company_id: UUID,
    body: ParseRawTransactionRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
    parser: GeminiParser = Depends(get_gemini_parser),
) -> AIParseResponseSchema:
    # -----------------------------------------------------------------------
    # 1. Валидация счёта (принадлежность компании + существование)
    # -----------------------------------------------------------------------
    account = await _load_account(db, body.account_id, company_id)

    # -----------------------------------------------------------------------
    # 2. Загрузка категорий для обогащения промпта
    # -----------------------------------------------------------------------
    available_categories = await _load_categories(db, company_id)

    # -----------------------------------------------------------------------
    # 3. AI-классификация (с backoff и fallback)
    # -----------------------------------------------------------------------
    gemini_result, is_fallback = await parser.classify_raw_text(
        text=body.raw_text,
        available_categories=available_categories,
    )

    # -----------------------------------------------------------------------
    # 4. Нормализация результата
    # -----------------------------------------------------------------------
    amount = _parse_amount_safe(gemini_result.amount)
    if amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "AI-парсер не смог извлечь сумму из текста. "
                "Уточните описание операции (добавьте числовое значение суммы)."
            ),
        )

    txn_type = _map_transaction_type(gemini_result.transaction_type)
    payment_date: date = body.payment_date or date.today()
    accrual_date: Optional[date] = body.accrual_date or payment_date
    category_id = await _find_category_id(
        db, company_id, gemini_result.suggested_category_slug
    )
    confidence = Decimal(
        str(gemini_result.confidence_score)
    ).quantize(Decimal("0.0001"))

    # -----------------------------------------------------------------------
    # 5. Создание записи Transaction
    # -----------------------------------------------------------------------
    transaction = Transaction(
        id=uuid4(),
        company_id=company_id,
        account_id=account.id,
        category_id=category_id,
        created_by_user_id=current_user.user_id,
        transaction_type=txn_type,
        status=TransactionStatus.CONFIRMED,
        amount=amount,
        currency=account.currency,
        exchange_rate=Decimal("1.000000"),
        amount_base_currency=amount,
        payment_date=payment_date,
        accrual_date=accrual_date,
        description=gemini_result.description,
        counterparty=gemini_result.counterparty,
        ai_classified=True,
        ai_confidence=confidence,
        tags=["ai_classified"] + (["fallback"] if is_fallback else []),
        meta={
            "raw_text": body.raw_text[:500],
            "parsed_at": datetime.now(tz=timezone.utc).isoformat(),
            "model": parser.model_id if not is_fallback else "regex_fallback",
        },
        is_deleted=False,
    )
    db.add(transaction)

    # -----------------------------------------------------------------------
    # 6. Обновление денормализованного баланса счёта
    # -----------------------------------------------------------------------
    _update_account_balance(account, amount, txn_type)

    # Flush перед commit для получения ID (commit делает сессия при выходе)
    await db.flush()

    # -----------------------------------------------------------------------
    # 7. Формирование ответа
    # -----------------------------------------------------------------------
    return AIParseResponseSchema(
        transaction_id=transaction.id,
        amount=amount,
        currency=account.currency.value,
        transaction_type=gemini_result.transaction_type,
        counterparty=gemini_result.counterparty,
        description=gemini_result.description,
        suggested_category_slug=gemini_result.suggested_category_slug,
        confidence_score=float(gemini_result.confidence_score),
        is_fallback=is_fallback,
        payment_date=payment_date,
        accrual_date=accrual_date,
    )
