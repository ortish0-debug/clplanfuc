"""Генерация налоговых деклараций и отчётов (Sprint 20)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company
from app.domain.models.ledger import JournalEntry, LedgerLine, AccountChart
from app.domain.models.taxes import VatRecord


async def generate_vat_declaration(
    db: AsyncSession,
    company_id: UUID,
    year: int,
    quarter: int,
) -> dict:
    """Генерирует декларацию по НДС на указанный квартал."""
    # Проверка company_id (IDOR)
    company = await db.execute(select(Company).where(Company.id == company_id))
    if not company.scalar_one_or_none():
        raise ValueError(f"Company {company_id} not found")

    # Границы квартала
    q_start_map = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}
    q_end_map = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

    q_start = date(year, int(q_start_map[quarter][:2]), int(q_start_map[quarter][3:]))
    q_end = date(year, int(q_end_map[quarter][:2]), int(q_end_map[quarter][3:]))

    # Запрос НДС реестра
    result = await db.execute(
        select(
            func.sum(VatRecord.vat_amount).label("total_vat"),
            VatRecord.is_input,
        ).where(
            and_(
                VatRecord.company_id == company_id,
                VatRecord.operation_date >= q_start,
                VatRecord.operation_date <= q_end,
            )
        ).group_by(VatRecord.is_input)
    )

    rows = result.all()
    total_output_vat = Decimal("0.00")
    total_input_vat = Decimal("0.00")

    for total_vat, is_input in rows:
        if total_vat:
            if is_input:
                total_input_vat = Decimal(str(total_vat))
            else:
                total_output_vat = Decimal(str(total_vat))

    net_vat_to_pay = (total_output_vat - total_input_vat).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if net_vat_to_pay < 0:
        net_vat_to_pay = Decimal("0.00")

    return {
        "year": year,
        "quarter": quarter,
        "total_output_vat": str(total_output_vat.quantize(Decimal("0.01"))),
        "total_input_vat": str(total_input_vat.quantize(Decimal("0.01"))),
        "net_vat_to_pay": str(net_vat_to_pay),
    }


async def generate_profit_tax_report(
    db: AsyncSession,
    company_id: UUID,
    year: int,
) -> dict:
    """Генерирует отчёт по налогу на прибыль за год."""
    # Проверка company_id (IDOR)
    company = await db.execute(select(Company).where(Company.id == company_id))
    if not company.scalar_one_or_none():
        raise ValueError(f"Company {company_id} not found")

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    # Получить счета по кодам (9001, 9002 для доходов/расходов)
    accounts_result = await db.execute(
        select(AccountChart.id, AccountChart.code).where(
            and_(
                AccountChart.company_id == company_id,
                AccountChart.code.in_(["9001", "9002", "9102"]),
            )
        )
    )
    accounts_map = {code: acc_id for acc_id, code in accounts_result.all()}

    # Выручка: кредит счета 9001
    revenue_result = await db.execute(
        select(func.sum(LedgerLine.credit)).where(
            and_(
                LedgerLine.account_chart_id == accounts_map.get("9001"),
                JournalEntry.id == LedgerLine.journal_entry_id,
                JournalEntry.company_id == company_id,
                JournalEntry.operation_date >= year_start,
                JournalEntry.operation_date <= year_end,
            )
        )
    )
    total_revenues = Decimal(str(revenue_result.scalar() or 0)).quantize(Decimal("0.01"))

    # Расходы: дебет счетов 9002 (себестоимость) + 9102 (прочие)
    expense_result = await db.execute(
        select(func.sum(LedgerLine.debit)).where(
            and_(
                LedgerLine.account_chart_id.in_([accounts_map.get("9002"), accounts_map.get("9102")]),
                JournalEntry.id == LedgerLine.journal_entry_id,
                JournalEntry.company_id == company_id,
                JournalEntry.operation_date >= year_start,
                JournalEntry.operation_date <= year_end,
            )
        )
    )
    total_expenses = Decimal(str(expense_result.scalar() or 0)).quantize(Decimal("0.01"))

    # Налоговая база и налог
    tax_base = (total_revenues - total_expenses).quantize(Decimal("0.01"))
    if tax_base < 0:
        tax_base = Decimal("0.00")

    tax_rate = Decimal("0.20")
    tax_amount = (tax_base * tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "year": year,
        "total_revenues": str(total_revenues),
        "total_expenses": str(total_expenses),
        "tax_base": str(tax_base),
        "tax_rate": str(tax_rate),
        "tax_amount": str(tax_amount),
    }
