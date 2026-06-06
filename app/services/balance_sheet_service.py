"""Управленческий баланс: расчёт по дебетам/кредитам (Спринт 16)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.ledger import AccountChart, AccountCategory, LedgerLine, JournalEntry
from app.domain.schemas.balance_sheet import (
    BalanceItemSchema, BalanceSectionSchema, BalanceSheetResponse
)

ZERO = Decimal("0.00")


async def get_balance_sheet(
    db: AsyncSession,
    company_id: UUID,
    target_date: date,
) -> BalanceSheetResponse:
    """
    Расчёт управленческого баланса на дату.
    Активы = Пассивы + Капитал (с учётом нераспределённой прибыли).
    """
    # 1. Загружаем счета компании
    accounts_result = await db.execute(
        select(AccountChart).where(AccountChart.company_id == company_id)
    )
    accounts = {acc.id: acc for acc in accounts_result.scalars().all()}

    # 2. Агрегируем дебиты/кредиты по счетам до целевой даты
    ledger_result = await db.execute(
        select(
            LedgerLine.account_chart_id,
            func.sum(LedgerLine.debit).label("total_debit"),
            func.sum(LedgerLine.credit).label("total_credit"),
        )
        .join(JournalEntry, LedgerLine.journal_entry_id == JournalEntry.id)
        .where(
            and_(
                JournalEntry.company_id == company_id,
                JournalEntry.operation_date <= target_date,
            )
        )
        .group_by(LedgerLine.account_chart_id)
    )

    account_balances = {}
    for row in ledger_result:
        debit = row.total_debit or ZERO
        credit = row.total_credit or ZERO
        account_balances[row.account_chart_id] = (debit, credit)

    # 3. Распределяем по разделам
    assets_items = []
    liabilities_items = []
    equity_items = []
    revenue_credit = ZERO
    expense_debit = ZERO

    for acc_id, acc in accounts.items():
        debit, credit = account_balances.get(acc_id, (ZERO, ZERO))

        if acc.category == AccountCategory.ASSET:
            amount = debit - credit
            if amount != ZERO:
                assets_items.append(BalanceItemSchema(name=acc.name, code=acc.code, amount=amount))

        elif acc.category == AccountCategory.LIABILITY:
            amount = credit - debit
            if amount != ZERO:
                liabilities_items.append(BalanceItemSchema(name=acc.name, code=acc.code, amount=amount))

        elif acc.category == AccountCategory.EQUITY:
            amount = credit - debit
            if amount != ZERO:
                equity_items.append(BalanceItemSchema(name=acc.name, code=acc.code, amount=amount))

        elif acc.category == AccountCategory.REVENUE:
            revenue_credit += credit

        elif acc.category == AccountCategory.EXPENSE:
            expense_debit += debit

    # 4. Нераспределённая прибыль (доходы - расходы)
    retained_earnings = revenue_credit - expense_debit
    if retained_earnings != ZERO:
        equity_items.append(
            BalanceItemSchema(name="Нераспределённая прибыль", code=None, amount=retained_earnings)
        )

    # 5. Создаём разделы
    total_assets = sum(item.amount for item in assets_items)
    total_liabilities = sum(item.amount for item in liabilities_items)
    total_equity = sum(item.amount for item in equity_items)
    total_liabilities_equity = total_liabilities + total_equity

    assets_section = BalanceSectionSchema(
        title="АКТИВЫ",
        items=assets_items,
        total=total_assets,
    )
    liabilities_section = BalanceSectionSchema(
        title="ОБЯЗАТЕЛЬСТВА",
        items=liabilities_items,
        total=total_liabilities,
    )
    equity_section = BalanceSectionSchema(
        title="КАПИТАЛ",
        items=equity_items,
        total=total_equity,
    )

    # 6. Проверяем баланс
    is_balanced = total_assets == total_liabilities_equity

    return BalanceSheetResponse(
        company_id=company_id,
        target_date=target_date,
        assets=[assets_section],
        liabilities=[liabilities_section],
        equity=[equity_section],
        total_assets=total_assets,
        total_liabilities_equity=total_liabilities_equity,
        is_balanced=is_balanced,
    )
