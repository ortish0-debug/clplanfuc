"""Sberbank Business API integration service (Sprint 22)."""
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType
# from app.infrastructure.api.v1.routers.bank_auth import get_decrypted_bank_tokens
from app.services.currency_service import convert_to_rub
from app.services.tinkoff_service import _auto_classify_description

logger = logging.getLogger(__name__)


async def sync_sberbank_statements(
    db: AsyncSession,
    company_id: UUID,
    account_id: UUID,
    from_date: date,
    to_date: date,
) -> int:
    """
    Sync transactions from Sberbank Business API for a given account.

    Args:
        db: Database session
        company_id: Company ID for IDOR protection
        account_id: Account ID to sync to
        from_date: Start date for statement
        to_date: End date for statement

    Returns:
        Number of successfully imported transactions
    """
    # Verify account ownership
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.company_id == company_id,
            Account.is_deleted.is_(False),
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        logger.error(f"Account {account_id} not found for company {company_id}")
        return 0

    # Get Sberbank OAuth tokens (mock for testing)
    try:
        access_token = "mock_sberbank_token_12345"
    except Exception as exc:
        logger.error(f"Failed to retrieve Sberbank tokens: {exc}")
        return 0

    # Stub: Mock Sberbank API response
    operations = await _mock_sberbank_api_call(
        access_token=access_token,
        from_date=from_date,
        to_date=to_date,
    )

    imported_count = 0
    for op in operations:
        try:
            # Deduplication check: skip if bank_transaction_id already exists
            if op.get("bank_transaction_id"):
                existing = await db.execute(
                    select(Transaction.id).where(
                        Transaction.bank_transaction_id == op["bank_transaction_id"]
                    )
                )
                if existing.scalar_one_or_none():
                    logger.info(
                        f"Duplicate transaction skipped: {op['bank_transaction_id']}"
                    )
                    continue

            # Multi-currency conversion
            rub_amount = await convert_to_rub(
                db,
                float(op["amount"]),
                account.currency,
                op["payment_date"],
            )

            # Calculate exchange rate
            exchange_rate = (
                Decimal(str(rub_amount / float(op["amount"])))
                if op["amount"] != 0
                else Decimal("1.000000")
            )

            # Create transaction
            txn_type = (
                TransactionType.INCOME
                if op["operation_type"] == "income"
                else TransactionType.EXPENSE
            )

            # Auto-classify by description keywords
            category_code = _auto_classify_description(op["description"])
            category_id = None
            ai_classified = False

            if category_code:
                from app.domain.models.finance import Category
                cat_result = await db.execute(
                    select(Category.id).where(Category.code == category_code)
                )
                category_id = cat_result.scalar_one_or_none()
                ai_classified = category_id is not None

            txn = Transaction(
                id=uuid.uuid4(),
                company_id=company_id,
                account_id=account_id,
                category_id=category_id,
                created_by_user_id=None,
                transaction_type=txn_type,
                status=TransactionStatus.CONFIRMED,
                amount=Decimal(str(op["amount"])),
                currency=account.currency,
                exchange_rate=exchange_rate,
                amount_base_currency=Decimal(str(rub_amount)),
                payment_date=op["payment_date"],
                accrual_date=op["payment_date"],
                description=op["description"],
                counterparty=op["counterparty"],
                bank_transaction_id=op["bank_transaction_id"],
                ai_classified=ai_classified,
                tags=["sberbank_sync"],
                meta={
                    "synced_at": datetime.now(tz=timezone.utc).isoformat(),
                    "sync_source": "sberbank_api",
                    "auto_category_code": category_code,
                },
                is_deleted=False,
            )
            db.add(txn)

            # Update account balance
            if txn_type == TransactionType.INCOME:
                account.current_balance += Decimal(str(op["amount"]))
            else:
                account.current_balance -= Decimal(str(op["amount"]))

            imported_count += 1
            logger.info(f"Imported transaction: {op['bank_transaction_id']}")

        except Exception as exc:
            logger.error(
                f"Failed to import transaction {op.get('bank_transaction_id')}: {exc}"
            )
            continue

    await db.flush()
    logger.info(f"Sberbank sync completed: {imported_count} transactions imported")
    return imported_count


async def _mock_sberbank_api_call(
    access_token: str,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    """
    Mock Sberbank API call for testing.

    In production, replace with:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.sberbank.ru/v1/statement",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"from": from_date.isoformat(), "to": to_date.isoformat()},
            )
            return response.json().get("operations", [])
    """
    logger.info(
        f"Mock Sberbank API: fetching operations from {from_date} to {to_date}"
    )

    # Stub data for testing
    return [
        {
            "bank_transaction_id": f"sber_{from_date.isoformat()}_001",
            "amount": 200000.00,
            "operation_type": "income",
            "payment_date": from_date,
            "description": "Поступление от ИП Петров за услуги",
            "counterparty": "ИП Петров",
        },
        {
            "bank_transaction_id": f"sber_{from_date.isoformat()}_002",
            "amount": 75000.00,
            "operation_type": "expense",
            "payment_date": from_date,
            "description": "Оплата налога в ФНС",
            "counterparty": "ФНС России",
        },
    ]
