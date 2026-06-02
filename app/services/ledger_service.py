"""Управленческий Учёт: Двойная запись и План счетов (Спринт 14)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.ledger import AccountChart, AccountCategory, JournalEntry, LedgerLine


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT CHART OF ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_ACCOUNTS = [
    ("5100", "Расчетные счета",           AccountCategory.ASSET),
    ("4100", "Товары на складе",          AccountCategory.ASSET),
    ("6000", "Расчеты с поставщиками",    AccountCategory.LIABILITY),
    ("6200", "Расчеты с покупателями",    AccountCategory.LIABILITY),
    ("9001", "Выручка",                   AccountCategory.REVENUE),
    ("9002", "Себестоимость продаж",      AccountCategory.EXPENSE),
]


async def initialize_default_chart(
    db: AsyncSession,
    company_id: UUID,
) -> None:
    """
    Инициализирует план счетов по умолчанию для новой компании.
    Пропускает счета, если они уже существуют (IDOR check).
    """
    # Проверяем, существуют ли хотя бы один счет для этой компании
    result = await db.execute(
        select(AccountChart).where(AccountChart.company_id == company_id).limit(1)
    )
    if result.scalar_one_or_none():
        # Счета уже инициализированы
        return

    # Создаём default accounts
    for code, name, category in DEFAULT_ACCOUNTS:
        chart = AccountChart(
            id=uuid.uuid4(),
            company_id=company_id,
            code=code,
            name=name,
            category=category,
            is_active=True,
        )
        db.add(chart)

    await db.flush()


async def post_double_entry(
    db: AsyncSession,
    company_id: UUID,
    date_: date,
    description: str,
    doc_type: str,
    doc_id: UUID,
    postings: list[dict],
) -> JournalEntry:
    """
    Создает проводку с двойной записью (double-entry).

    Args:
        db: AsyncSession
        company_id: UUID компании (IDOR)
        date_: дата операции
        description: описание
        doc_type: тип документа-триггера (invoice, stock_op, etc.)
        doc_id: UUID документа-триггера
        postings: список [{"code": "5100", "debit": 100, "credit": 0}, ...]

    Returns:
        JournalEntry (со строками LedgerLine)

    Raises:
        HTTP 422: если SUM(debit) != SUM(credit)
        HTTP 404: если счет не найден в плане счетов компании
    """
    if not postings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="postings must not be empty",
        )

    # Проверяем баланс: SUM(debit) == SUM(credit)
    total_debit = sum(Decimal(str(p.get("debit", 0))) for p in postings)
    total_credit = sum(Decimal(str(p.get("credit", 0))) for p in postings)

    if total_debit != total_credit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Debit sum ({total_debit}) != Credit sum ({total_credit})",
        )

    # Загружаем счета компании по кодам (IDOR: company_id)
    codes = [p["code"] for p in postings]
    accounts_result = await db.execute(
        select(AccountChart).where(
            and_(
                AccountChart.company_id == company_id,
                AccountChart.code.in_(codes),
            )
        )
    )
    accounts_map = {acc.code: acc.id for acc in accounts_result.scalars().all()}

    # Проверяем, что все счета найдены
    for posting in postings:
        if posting["code"] not in accounts_map:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Account {posting['code']} not found for company",
            )

    # Создаём JournalEntry
    entry = JournalEntry(
        id=uuid.uuid4(),
        company_id=company_id,
        operation_date=date_,
        description=description,
        source_doc_type=doc_type,
        source_doc_id=doc_id,
    )
    db.add(entry)
    await db.flush()  # Получаем ID entry

    # Создаём LedgerLine для каждого posting
    for posting in postings:
        line = LedgerLine(
            id=uuid.uuid4(),
            journal_entry_id=entry.id,
            account_chart_id=accounts_map[posting["code"]],
            debit=Decimal(str(posting.get("debit", 0))),
            credit=Decimal(str(posting.get("credit", 0))),
        )
        db.add(line)

    await db.flush()
    return entry
