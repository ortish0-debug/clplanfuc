"""
Сервис: Управленческий Баланс (Balance Sheet).

Реализует уравнение двойной записи:
    АКТИВЫ = КАПИТАЛ + ОБЯЗАТЕЛЬСТВА

Структура баланса (управленческий формат):
    I.  Внеоборотные активы   — Основные средства (упрощённо = 0)
    II. Оборотные активы      — Денежные средства + Дебиторка
   ─────────────────────────────────────────────────────────────
   ИТОГО АКТИВОВ
   ─────────────────────────────────────────────────────────────
   III. Капитал               — Уставной капитал + Нераспр. прибыль
    IV. Долгосрочные обяз-ва  — (упрощённо = 0)
     V. Краткосрочные обяз-ва — Кредиторская задолженность
   ─────────────────────────────────────────────────────────────
   ИТОГО ПАССИВОВ
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.counterparties import ContractOrInvoice, InvoiceStatus, InvoiceType
from app.domain.models.finance import Account, Transaction, TransactionType
from app.domain.schemas.balance_sheet import (
    BalanceItemSchema,
    BalanceSectionSchema,
    BalanceSheetResponse,
    CounterpartyDebt,
    DebtSummaryResponse,
)

ZERO = Decimal("0.00")


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _calc_cash_on_date(
    db: AsyncSession,
    company_id: UUID,
    on_date: date,
) -> Decimal:
    """
    Денежные средства на расчётных счетах и в кассе на дату.

    Формула:  SUM(initial_balance) по счетам
            + SUM(amount WHERE type=INCOME  AND payment_date <= on_date)
            − SUM(amount WHERE type=EXPENSE AND payment_date <= on_date)
    """
    # Начальные остатки
    init_result = await db.execute(
        select(func.coalesce(func.sum(Account.initial_balance), ZERO))
        .where(
            and_(
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
    )
    initial_total: Decimal = init_result.scalar() or ZERO

    # Входящие транзакции (INCOME) до on_date
    in_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_base_currency), ZERO))
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.INCOME,
                Transaction.payment_date <= on_date,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    total_income: Decimal = in_result.scalar() or ZERO

    # Исходящие транзакции (EXPENSE) до on_date
    out_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_base_currency), ZERO))
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.payment_date <= on_date,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    total_expense: Decimal = out_result.scalar() or ZERO

    return (initial_total + total_income - total_expense).quantize(Decimal("0.01"))


async def _calc_accounts_receivable(
    db: AsyncSession,
    company_id: UUID,
    on_date: date,
) -> Decimal:
    """Дебиторская задолженность: сумма неоплаченных выставленных счетов."""
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(ContractOrInvoice.total_amount - ContractOrInvoice.paid_amount),
                ZERO,
            )
        )
        .where(
            and_(
                ContractOrInvoice.company_id == company_id,
                ContractOrInvoice.invoice_type == InvoiceType.CUSTOMER_INVOICE,
                ContractOrInvoice.status != InvoiceStatus.PAID,
                ContractOrInvoice.status != InvoiceStatus.CANCELLED,
                ContractOrInvoice.date <= on_date,
                ContractOrInvoice.is_deleted.is_(False),
            )
        )
    )
    return (result.scalar() or ZERO).quantize(Decimal("0.01"))


async def _calc_accounts_payable(
    db: AsyncSession,
    company_id: UUID,
    on_date: date,
) -> Decimal:
    """Кредиторская задолженность: сумма неоплаченных полученных счетов."""
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(ContractOrInvoice.total_amount - ContractOrInvoice.paid_amount),
                ZERO,
            )
        )
        .where(
            and_(
                ContractOrInvoice.company_id == company_id,
                ContractOrInvoice.invoice_type == InvoiceType.SUPPLIER_BILL,
                ContractOrInvoice.status != InvoiceStatus.PAID,
                ContractOrInvoice.status != InvoiceStatus.CANCELLED,
                ContractOrInvoice.date <= on_date,
                ContractOrInvoice.is_deleted.is_(False),
            )
        )
    )
    return (result.scalar() or ZERO).quantize(Decimal("0.01"))


async def _calc_retained_earnings(
    db: AsyncSession,
    company_id: UUID,
    on_date: date,
) -> Decimal:
    """
    Нераспределённая прибыль = накопленный чистый доход по дате начисления.
    P&L-разрез: accrual_date (не payment_date).
    """
    in_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_base_currency), ZERO))
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.INCOME,
                Transaction.accrual_date <= on_date,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    out_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount_base_currency), ZERO))
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.accrual_date <= on_date,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    return ((in_result.scalar() or ZERO) - (out_result.scalar() or ZERO)).quantize(
        Decimal("0.01")
    )


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def calculate_balance_sheet(
    company_id: UUID,
    on_date: date,
    db: AsyncSession,
    currency: str = "RUB",
) -> BalanceSheetResponse:
    """
    Строит полный Управленческий Баланс на указанную дату.

    Возвращает BalanceSheetResponse со структурой активов, пассивов
    и признаком сходимости (is_balanced).
    """
    # ── Запросы можно выполнять параллельно через asyncio.gather,
    #    но здесь для простоты — последовательно
    cash         = await _calc_cash_on_date(db, company_id, on_date)
    ar           = await _calc_accounts_receivable(db, company_id, on_date)
    ap           = await _calc_accounts_payable(db, company_id, on_date)
    retained     = await _calc_retained_earnings(db, company_id, on_date)

    # ── I. Внеоборотные активы (упрощённо — нет учёта ОС) ─────────────────
    fixed = BalanceSectionSchema(
        section_code="I",
        section_name="Внеоборотные активы",
        items=[
            BalanceItemSchema(code="110", name="Основные средства",           amount=ZERO, note="Учёт ОС не ведётся"),
            BalanceItemSchema(code="120", name="Нематериальные активы",        amount=ZERO),
        ],
        total=ZERO,
    )

    # ── II. Оборотные активы ───────────────────────────────────────────────
    current_assets_total = cash + ar
    current_assets = BalanceSectionSchema(
        section_code="II",
        section_name="Оборотные активы",
        items=[
            BalanceItemSchema(code="210", name="Денежные средства",           amount=cash),
            BalanceItemSchema(code="220", name="Дебиторская задолженность",   amount=ar),
            BalanceItemSchema(code="230", name="Запасы",                      amount=ZERO, note="Учёт запасов не ведётся"),
        ],
        total=current_assets_total,
    )

    total_assets = fixed.total + current_assets.total

    # ── III. Капитал ───────────────────────────────────────────────────────
    share_capital = ZERO  # Уставной капитал: можно хранить в company.settings
    equity_total  = share_capital + retained
    equity = BalanceSectionSchema(
        section_code="III",
        section_name="Капитал и резервы",
        items=[
            BalanceItemSchema(code="310", name="Уставной капитал",            amount=share_capital),
            BalanceItemSchema(code="320", name="Нераспределённая прибыль",    amount=retained),
        ],
        total=equity_total,
    )

    # ── IV. Долгосрочные обязательства ─────────────────────────────────────
    lt_liabilities = BalanceSectionSchema(
        section_code="IV",
        section_name="Долгосрочные обязательства",
        items=[
            BalanceItemSchema(code="410", name="Долгосрочные кредиты",        amount=ZERO, note="Учёт кредитов не ведётся"),
        ],
        total=ZERO,
    )

    # ── V. Краткосрочные обязательства ────────────────────────────────────
    current_liabilities = BalanceSectionSchema(
        section_code="V",
        section_name="Краткосрочные обязательства",
        items=[
            BalanceItemSchema(code="510", name="Кредиторская задолженность",  amount=ap),
            BalanceItemSchema(code="520", name="Краткосрочные кредиты",       amount=ZERO),
        ],
        total=ap,
    )

    total_liabilities_equity = equity.total + lt_liabilities.total + current_liabilities.total

    difference  = (total_assets - total_liabilities_equity).quantize(Decimal("0.01"))
    is_balanced = abs(difference) < Decimal("0.01")

    return BalanceSheetResponse(
        on_date=on_date,
        currency=currency,
        fixed_assets=fixed,
        current_assets=current_assets,
        total_assets=total_assets,
        equity=equity,
        long_term_liabilities=lt_liabilities,
        current_liabilities=current_liabilities,
        total_liabilities_equity=total_liabilities_equity,
        is_balanced=is_balanced,
        balance_difference=abs(difference),
    )


async def calculate_debt_summary(
    company_id: UUID,
    as_of_date: date,
    db: AsyncSession,
    currency: str = "RUB",
    top_n: int = 5,
) -> DebtSummaryResponse:
    """
    Сводка задолженностей по контрагентам.
    Возвращает топ-N должников (дебиторка) и кредиторов (кредиторка).
    """
    from app.domain.models.counterparties import Counterparty
    from sqlalchemy.orm import aliased

    cp = aliased(Counterparty)

    # Агрегируем по counterparty_id
    rows = await db.execute(
        select(
            ContractOrInvoice.counterparty_id,
            ContractOrInvoice.invoice_type,
            func.sum(ContractOrInvoice.total_amount - ContractOrInvoice.paid_amount).label("outstanding"),
            func.count(ContractOrInvoice.id).label("cnt"),
        )
        .where(
            and_(
                ContractOrInvoice.company_id == company_id,
                ContractOrInvoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.PARTIALLY_PAID]),
                ContractOrInvoice.date <= as_of_date,
                ContractOrInvoice.is_deleted.is_(False),
            )
        )
        .group_by(ContractOrInvoice.counterparty_id, ContractOrInvoice.invoice_type)
    )

    # Группируем по контрагенту
    cp_map: dict[UUID, dict] = {}
    for row in rows.all():
        cid = row.counterparty_id
        if cid not in cp_map:
            cp_map[cid] = {"ar": ZERO, "ap": ZERO, "cnt": 0}
        if row.invoice_type == InvoiceType.CUSTOMER_INVOICE:
            cp_map[cid]["ar"] += row.outstanding or ZERO
        else:
            cp_map[cid]["ap"] += row.outstanding or ZERO
        cp_map[cid]["cnt"] += row.cnt

    if not cp_map:
        return DebtSummaryResponse(
            as_of_date=as_of_date,
            currency=currency,
            total_accounts_receivable=ZERO,
            total_accounts_payable=ZERO,
            net_position=ZERO,
            top_debtors=[],
            top_creditors=[],
            all_debts=[],
        )

    # Подтягиваем имена контрагентов
    cp_result = await db.execute(
        select(Counterparty.id, Counterparty.name, Counterparty.inn)
        .where(Counterparty.id.in_(cp_map.keys()))
    )
    cp_names = {r.id: (r.name, r.inn) for r in cp_result.all()}

    # Строим список объектов
    debts: list[CounterpartyDebt] = []
    for cp_id, data in cp_map.items():
        name, inn = cp_names.get(cp_id, ("Неизвестный", None))
        ar = data["ar"].quantize(Decimal("0.01"))
        ap = data["ap"].quantize(Decimal("0.01"))
        debts.append(CounterpartyDebt(
            counterparty_id=cp_id,
            name=name,
            inn=inn,
            accounts_receivable=ar,
            accounts_payable=ap,
            net_position=(ar - ap).quantize(Decimal("0.01")),
            invoice_count=data["cnt"],
        ))

    total_ar = sum((d.accounts_receivable for d in debts), ZERO).quantize(Decimal("0.01"))
    total_ap = sum((d.accounts_payable    for d in debts), ZERO).quantize(Decimal("0.01"))

    top_debtors   = sorted(debts, key=lambda d: d.accounts_receivable, reverse=True)[:top_n]
    top_creditors = sorted(debts, key=lambda d: d.accounts_payable,    reverse=True)[:top_n]

    return DebtSummaryResponse(
        as_of_date=as_of_date,
        currency=currency,
        total_accounts_receivable=total_ar,
        total_accounts_payable=total_ap,
        net_position=(total_ar - total_ap).quantize(Decimal("0.01")),
        top_debtors=top_debtors,
        top_creditors=top_creditors,
        all_debts=sorted(debts, key=lambda d: d.net_position, reverse=True),
    )
