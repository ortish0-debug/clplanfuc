"""
Report generation: P&L, Cash Flow, Balance Sheet.
Все цифры читаются из реальных транзакций компании.
"""
from datetime import date
from uuid import UUID

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account, Category, Transaction,
    TransactionType, TransactionStatus,
)


# Статусы которые учитываются в отчётах
_ACTIVE = (TransactionStatus.CONFIRMED, TransactionStatus.RECONCILED)


async def _sum_by_type(
    db: AsyncSession,
    company_id: UUID,
    tx_type: TransactionType,
    start_date: date,
    end_date: date,
) -> float:
    """Сумма транзакций заданного типа за период."""
    result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == tx_type,
                Transaction.payment_date >= start_date,
                Transaction.payment_date <= end_date,
                Transaction.status.in_(_ACTIVE),
                Transaction.is_deleted == False,
            )
        )
    )
    return float(result.scalar() or 0)


async def _sum_by_category(
    db: AsyncSession,
    company_id: UUID,
    tx_type: TransactionType,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Разбивка суммы по категориям."""
    result = await db.execute(
        select(
            Category.name,
            Category.id,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .join(Category, Transaction.category_id == Category.id, isouter=True)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == tx_type,
                Transaction.payment_date >= start_date,
                Transaction.payment_date <= end_date,
                Transaction.status.in_(_ACTIVE),
                Transaction.is_deleted == False,
            )
        )
        .group_by(Category.id, Category.name)
        .order_by(func.sum(Transaction.amount).desc())
    )
    return [
        {"category_id": str(row.id) if row.id else None,
         "category_name": row.name or "Без категории",
         "amount": float(row.total)}
        for row in result.all()
    ]


async def generate_pl_report(
    db: AsyncSession,
    company_id: UUID,
    start_date: date,
    end_date: date,
) -> dict:
    """
    P&L (Отчёт о прибылях и убытках).

    Бизнес-логика:
      Выручка   = все INCOME-транзакции за период
      Расходы   = все EXPENSE-транзакции за период
      Прибыль   = Выручка − Расходы
      Налог     = Прибыль × 20% (если прибыль > 0)
      Чистая пр.= Прибыль − Налог
    """
    revenue = await _sum_by_type(db, company_id, TransactionType.INCOME,  start_date, end_date)
    opex    = await _sum_by_type(db, company_id, TransactionType.EXPENSE, start_date, end_date)

    gross_profit = revenue          # COGS = 0 (сервисный бизнес)
    ebitda       = revenue - opex
    taxes        = max(ebitda * 0.20, 0)
    net_income   = ebitda - taxes

    margin = round(net_income / revenue * 100, 1) if revenue > 0 else 0.0

    income_breakdown  = await _sum_by_category(db, company_id, TransactionType.INCOME,  start_date, end_date)
    expense_breakdown = await _sum_by_category(db, company_id, TransactionType.EXPENSE, start_date, end_date)

    return {
        "revenue":           revenue,
        "cogs":              0.0,
        "gross_profit":      gross_profit,
        "opex":              opex,
        "ebitda":            ebitda,
        "taxes":             taxes,
        "net_income":        net_income,
        "net_margin_pct":    margin,
        "income_breakdown":  income_breakdown,
        "expense_breakdown": expense_breakdown,
    }


async def generate_cash_flow_report(
    db: AsyncSession,
    company_id: UUID,
    start_date: date,
    end_date: date,
) -> dict:
    """
    ДДС (Отчёт о движении денежных средств).

    Бизнес-логика:
      Операционный CF = Поступления − Выплаты за период
      Остаток на начало = текущий баланс всех счетов − net_cf
      Остаток на конец  = текущий баланс всех счетов
    """
    inflow  = await _sum_by_type(db, company_id, TransactionType.INCOME,  start_date, end_date)
    outflow = await _sum_by_type(db, company_id, TransactionType.EXPENSE, start_date, end_date)
    net_cf  = inflow - outflow

    # Текущий суммарный баланс счетов
    bal_result = await db.execute(
        select(func.coalesce(func.sum(Account.current_balance), 0)).where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted == False,
            )
        )
    )
    current_balance   = float(bal_result.scalar() or 0)
    opening_balance   = current_balance - net_cf

    return {
        "operating_cf":     net_cf,
        "investing_cf":     0.0,
        "financing_cf":     0.0,
        "net_cash_flow":    net_cf,
        "inflow":           inflow,
        "outflow":          outflow,
        "opening_balance":  opening_balance,
        "closing_balance":  current_balance,
    }


async def generate_balance_sheet(
    db: AsyncSession,
    company_id: UUID,
) -> dict:
    """
    Баланс (упрощённый).

    Активы:
      Денежные средства = сумма остатков по всем счетам
    Пассивы:
      Нераспределённая прибыль = накопленный net_income за всё время
    """
    # Денежные средства — реальные остатки по счетам
    cash_result = await db.execute(
        select(func.coalesce(func.sum(Account.current_balance), 0)).where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted == False,
            )
        )
    )
    cash = float(cash_result.scalar() or 0)

    # Накопленная прибыль за всё время
    all_income = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.INCOME,
                Transaction.status.in_(_ACTIVE),
                Transaction.is_deleted == False,
            )
        )
    )
    all_expense = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.status.in_(_ACTIVE),
                Transaction.is_deleted == False,
            )
        )
    )
    total_income  = float(all_income.scalar() or 0)
    total_expense = float(all_expense.scalar() or 0)
    retained      = (total_income - total_expense) * 0.80  # после налогов

    total_assets  = cash
    total_passive = retained if retained > 0 else total_assets

    return {
        "assets": {
            "cash":         cash,
            "inventory":    0.0,
            "total_assets": total_assets,
        },
        "liabilities_equity": {
            "retained_earnings": retained,
            "total_passives":    total_passive,
        },
        "balanced": abs(total_assets - total_passive) < total_assets * 0.01 if total_assets > 0 else True,
    }


async def generate_financial_ratios(
    db: AsyncSession,
    company_id: UUID,
    start_date: date,
    end_date: date,
) -> dict:
    """Финансовые коэффициенты на основе реального P&L и баланса."""
    pl = await generate_pl_report(db, company_id, start_date, end_date)
    bs = await generate_balance_sheet(db, company_id)

    revenue = pl["revenue"]
    ebitda  = pl["ebitda"]
    net_inc = pl["net_income"]
    total_p = bs["liabilities_equity"]["total_passives"]

    ebitda_margin = round(ebitda  / revenue * 100, 1) if revenue > 0 else 0.0
    net_margin    = round(net_inc / revenue * 100, 1) if revenue > 0 else 0.0
    roe           = round(net_inc / total_p * 100, 1) if total_p > 0 else 0.0
    roi           = round(ebitda  / max(total_p, 1)  * 100, 1)

    if ebitda_margin > 25 and roe > 20:
        health = "EXCELLENT"
    elif ebitda_margin > 10:
        health = "GOOD"
    elif ebitda_margin > 0:
        health = "WARNING"
    else:
        health = "CRITICAL"

    return {
        "ebitda_margin": ebitda_margin,
        "net_margin":    net_margin,
        "roe":           roe,
        "roi":           roi,
        "health_score":  health,
    }
