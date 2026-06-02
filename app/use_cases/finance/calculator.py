"""
Финансовый движок: агрегация ДДС, P&L и 3-Way Balance.

Поддерживаемые бизнес-модели: standard, saas, milestone.
Налоговые режимы РФ: УСН 6%, УСН 15%, ОСНО.
Все денежные вычисления — исключительно через Decimal.
"""
from __future__ import annotations

import calendar
import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account,
    AccountType,
    Category,
    CategoryType,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domain.models.projects import PaymentRequest, PaymentRequestStatus

# ---------------------------------------------------------------------------
# Константы и перечисления
# ---------------------------------------------------------------------------

ZERO = Decimal("0.00")
INSURANCE_RATE = Decimal("0.30")          # Страховые взносы РФ 30%
PERSONAL_INCOME_TAX = Decimal("0.13")     # НДФЛ 13%
VAT_RATE = Decimal("0.20")                # НДС 20% (для ОСНО)
INCOME_TAX_OSNO = Decimal("0.20")         # Налог на прибыль ОСНО 20%
USN_INCOME_RATE = Decimal("0.06")         # УСН «Доходы» 6%
USN_PROFIT_RATE = Decimal("0.15")         # УСН «Доходы − Расходы» 15%


class BusinessModel(str, enum.Enum):
    STANDARD = "standard"       # Обычные отгрузки / товары / услуги
    SAAS = "saas"               # Подписная модель: MRR, ARR, Churn
    MILESTONE = "milestone"     # Проектная: поэтапные выплаты по вехам


class TaxRegime(str, enum.Enum):
    USN_INCOME = "usn_income"           # УСН «Доходы» 6%
    USN_INCOME_MINUS_EXPENSES = "usn_income_minus_expenses"  # УСН «Д−Р» 15%
    OSNO = "osno"                       # Общая система налогообложения


# ---------------------------------------------------------------------------
# Структуры данных результата
# ---------------------------------------------------------------------------


@dataclass
class MonthlyPeriod:
    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])


@dataclass
class CashFlowMonth:
    period: MonthlyPeriod
    inflow: Decimal = ZERO              # Приток: подтверждённые поступления
    outflow: Decimal = ZERO             # Отток: подтверждённые расходы
    net_cash_flow: Decimal = ZERO       # Чистый денежный поток (NCF)
    opening_balance: Decimal = ZERO     # Остаток на начало месяца
    closing_balance: Decimal = ZERO     # Остаток на конец месяца
    cash_gap: Decimal = ZERO            # Кассовый разрыв (< 0 — дефицит)
    transfer_in: Decimal = ZERO         # Входящие переводы между счетами
    transfer_out: Decimal = ZERO        # Исходящие переводы между счетами
    # Прогноз расходов из одобренных заявок (PaymentRequest.status='approved')
    # Учитываются только заявки, у которых planned_date попадает в данный период
    # и ещё не конвертированные в транзакцию (paid_transaction_id IS NULL)
    approved_requests_outflow: Decimal = ZERO


@dataclass
class PnLMonth:
    period: MonthlyPeriod
    revenue: Decimal = ZERO             # Выручка (начисленная)
    cost_of_goods: Decimal = ZERO       # Себестоимость / прямые затраты
    gross_profit: Decimal = ZERO        # Валовая прибыль
    operating_expenses: Decimal = ZERO  # Операционные расходы (без налогов и %%)
    ebitda: Decimal = ZERO              # EBITDA
    depreciation: Decimal = ZERO        # Амортизация
    ebit: Decimal = ZERO                # Операционная прибыль
    interest_expense: Decimal = ZERO    # Проценты по кредитам
    ebt: Decimal = ZERO                 # Прибыль до налогов
    tax: Decimal = ZERO                 # Налог (по режиму компании)
    net_profit: Decimal = ZERO          # Чистая прибыль
    payroll_gross: Decimal = ZERO       # ФОТ «грязными»
    payroll_insurance: Decimal = ZERO   # Страховые взносы 30%
    payroll_total: Decimal = ZERO       # Общая нагрузка на ФОТ


@dataclass
class BalanceSheet:
    """Упрощённый трёхстатейный баланс (3-Way Forecast)."""
    period: MonthlyPeriod
    # Активы
    cash_and_equivalents: Decimal = ZERO    # Деньги на счетах
    accounts_receivable: Decimal = ZERO     # Дебиторская задолженность
    total_assets: Decimal = ZERO
    # Обязательства
    accounts_payable: Decimal = ZERO        # Кредиторская задолженность
    short_term_debt: Decimal = ZERO         # Краткосрочные кредиты
    tax_payable: Decimal = ZERO             # Начисленные, но не уплаченные налоги
    total_liabilities: Decimal = ZERO
    # Капитал
    equity: Decimal = ZERO                  # Собственный капитал (Активы − Обязательства)


@dataclass
class SaaSMetrics:
    """SaaS-специфические метрики — заполняются только при business_model=saas."""
    period: MonthlyPeriod
    mrr: Decimal = ZERO                 # Monthly Recurring Revenue
    arr: Decimal = ZERO                 # Annual Run Rate = MRR × 12
    new_mrr: Decimal = ZERO             # MRR от новых клиентов
    churned_mrr: Decimal = ZERO         # Потерянный MRR
    expansion_mrr: Decimal = ZERO       # MRR от расширения
    churn_rate: Decimal = ZERO          # Коэффициент оттока (0.0 – 1.0)
    customers_start: int = 0
    customers_end: int = 0


@dataclass
class MilestoneMetrics:
    """Метрики проектных этапов — заполняются только при business_model=milestone."""
    period: MonthlyPeriod
    invoiced: Decimal = ZERO            # Выставлено счетов (начислено по вехам)
    collected: Decimal = ZERO           # Фактически получено
    backlog: Decimal = ZERO             # Невыставленный портфель работ
    milestone_count_completed: int = 0


@dataclass
class FinancialReport:
    company_id: UUID
    start_date: date
    end_date: date
    business_model: BusinessModel
    tax_regime: TaxRegime
    currency: str
    cash_flow: list[CashFlowMonth] = field(default_factory=list)
    pnl: list[PnLMonth] = field(default_factory=list)
    balance: list[BalanceSheet] = field(default_factory=list)
    saas_metrics: list[SaaSMetrics] = field(default_factory=list)
    milestone_metrics: list[MilestoneMetrics] = field(default_factory=list)
    # Итоговые суммарные показатели за весь период
    totals: dict[str, Decimal] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _build_periods(start_date: date, end_date: date) -> list[MonthlyPeriod]:
    """Строит список месячных периодов от start_date до end_date включительно."""
    periods: list[MonthlyPeriod] = []
    year, month = start_date.year, start_date.month
    while date(year, month, 1) <= date(end_date.year, end_date.month, 1):
        periods.append(MonthlyPeriod(year=year, month=month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods


def _get_tax_regime(company_settings: dict) -> TaxRegime:
    raw = company_settings.get("tax_regime", TaxRegime.USN_INCOME.value)
    try:
        return TaxRegime(raw)
    except ValueError:
        return TaxRegime.USN_INCOME


def _get_business_model(company_settings: dict) -> BusinessModel:
    raw = company_settings.get("business_model", BusinessModel.STANDARD.value)
    try:
        return BusinessModel(raw)
    except ValueError:
        return BusinessModel.STANDARD


# ---------------------------------------------------------------------------
# Налоговый калькулятор
# ---------------------------------------------------------------------------


class TaxCalculator:
    """
    Рассчитывает налоговую нагрузку по режиму налогообложения РФ.
    Для ОСНО учитывает НДС и налог на прибыль раздельно.
    """

    def __init__(self, regime: TaxRegime) -> None:
        self.regime = regime

    def calculate(
        self,
        revenue: Decimal,
        expenses: Decimal,
        payroll_total: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Возвращает (tax_amount, vat_amount).
        vat_amount > 0 только для ОСНО.
        """
        if self.regime == TaxRegime.USN_INCOME:
            # Налог 6% от выручки; взносы за сотрудников снижают налог до 50%
            tax_before_deduction = _round(revenue * USN_INCOME_RATE)
            max_deduction = _round(tax_before_deduction * Decimal("0.50"))
            actual_deduction = min(payroll_total * INSURANCE_RATE, max_deduction)
            return _round(max(ZERO, tax_before_deduction - actual_deduction)), ZERO

        if self.regime == TaxRegime.USN_INCOME_MINUS_EXPENSES:
            # Налог 15% от (доходы − расходы); минимальный налог 1% от выручки
            taxable = revenue - expenses - payroll_total
            standard_tax = _round(max(ZERO, taxable) * USN_PROFIT_RATE)
            minimum_tax = _round(revenue * Decimal("0.01"))
            return max(standard_tax, minimum_tax), ZERO

        if self.regime == TaxRegime.OSNO:
            # НДС 20% начисляется на выручку (упрощённо — без вычетов)
            vat = _round(revenue * VAT_RATE / (Decimal("1") + VAT_RATE))
            # Налог на прибыль 20% от (выручка без НДС − расходы − ФОТ)
            revenue_net = revenue - vat
            profit_taxable = max(ZERO, revenue_net - expenses - payroll_total)
            income_tax = _round(profit_taxable * INCOME_TAX_OSNO)
            return income_tax, vat

        return ZERO, ZERO


# ---------------------------------------------------------------------------
# Расчёт ФОТ
# ---------------------------------------------------------------------------


@dataclass
class PayrollResult:
    gross: Decimal        # «Грязная» зарплата (до НДФЛ)
    insurance: Decimal    # Страховые взносы 30% (за счёт работодателя)
    net: Decimal          # «Чистая» зарплата (после НДФЛ)
    total_employer_cost: Decimal  # Полная стоимость для компании


def calculate_payroll(gross_salary: Decimal) -> PayrollResult:
    """
    Рассчитывает полную нагрузку ФОТ для официальных сотрудников РФ.
    Страховые взносы: 30% сверх оклада (за счёт работодателя).
    НДФЛ: 13% из оклада (удерживается из зарплаты).
    """
    insurance = _round(gross_salary * INSURANCE_RATE)
    personal_income_tax = _round(gross_salary * PERSONAL_INCOME_TAX)
    net = _round(gross_salary - personal_income_tax)
    total_employer_cost = _round(gross_salary + insurance)
    return PayrollResult(
        gross=gross_salary,
        insurance=insurance,
        net=net,
        total_employer_cost=total_employer_cost,
    )


# ---------------------------------------------------------------------------
# Запросы к базе данных
# ---------------------------------------------------------------------------


async def _fetch_transactions_by_payment_date(
    db: AsyncSession,
    company_id: UUID,
    date_from: date,
    date_to: date,
) -> list[Transaction]:
    """ДДС: выбираем подтверждённые транзакции по дате платежа."""
    result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.status == TransactionStatus.CONFIRMED,
                Transaction.is_deleted.is_(False),
                Transaction.payment_date >= date_from,
                Transaction.payment_date <= date_to,
            )
        )
    )
    return list(result.scalars().all())


async def _fetch_transactions_by_accrual_date(
    db: AsyncSession,
    company_id: UUID,
    date_from: date,
    date_to: date,
) -> list[Transaction]:
    """P&L: выбираем транзакции по дате начисления (accrual_date)."""
    result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.status == TransactionStatus.CONFIRMED,
                Transaction.is_deleted.is_(False),
                Transaction.accrual_date >= date_from,
                Transaction.accrual_date <= date_to,
                Transaction.accrual_date.isnot(None),
            )
        )
    )
    return list(result.scalars().all())


async def _fetch_account_balances(
    db: AsyncSession,
    company_id: UUID,
    as_of_date: date,
) -> Decimal:
    """
    Суммирует current_balance всех активных счетов компании.
    Для точного исторического баланса — пересчитываем накопительно
    через начальный баланс + сумму операций до указанной даты.
    """
    # Начальные балансы всех счетов
    accounts_result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
    )
    accounts = list(accounts_result.scalars().all())
    if not accounts:
        return ZERO

    total_balance = ZERO
    for account in accounts:
        balance = account.initial_balance

        # Суммируем все подтверждённые операции до as_of_date
        txn_result = await db.execute(
            select(Transaction).where(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.is_deleted.is_(False),
                    Transaction.payment_date <= as_of_date,
                )
            )
        )
        for txn in txn_result.scalars().all():
            if txn.transaction_type == TransactionType.INCOME:
                balance += txn.amount_base_currency
            elif txn.transaction_type == TransactionType.EXPENSE:
                balance -= txn.amount_base_currency
            elif txn.transaction_type == TransactionType.TRANSFER:
                # Исходящий счёт
                balance -= txn.amount_base_currency

        # Входящие переводы
        transfer_in_result = await db.execute(
            select(func.coalesce(func.sum(Transaction.amount_base_currency), ZERO)).where(
                and_(
                    Transaction.destination_account_id == account.id,
                    Transaction.transaction_type == TransactionType.TRANSFER,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.is_deleted.is_(False),
                    Transaction.payment_date <= as_of_date,
                )
            )
        )
        balance += Decimal(str(transfer_in_result.scalar() or "0"))
        total_balance += balance

    return _round(total_balance)


async def _fetch_approved_payment_requests(
    db: AsyncSession,
    company_id: UUID,
    date_from: date,
    date_to: date,
) -> list[PaymentRequest]:
    """
    Возвращает одобренные заявки (status=APPROVED, paid_transaction_id IS NULL),
    у которых planned_date попадает в диапазон [date_from, date_to].

    Используется для прогноза оттока денег на будущие периоды платёжного
    календаря: заявки согласованы, но транзакция ещё не создана.
    """
    result = await db.execute(
        select(PaymentRequest).where(
            and_(
                PaymentRequest.company_id == company_id,
                PaymentRequest.status == PaymentRequestStatus.APPROVED,
                PaymentRequest.paid_transaction_id.is_(None),
                PaymentRequest.planned_date >= date_from,
                PaymentRequest.planned_date <= date_to,
            )
        )
    )
    return list(result.scalars().all())


async def _fetch_categories_map(
    db: AsyncSession,
    company_id: UUID,
) -> dict[UUID, Category]:
    result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id == company_id,
                Category.is_deleted.is_(False),
            )
        )
    )
    return {cat.id: cat for cat in result.scalars().all()}


# ---------------------------------------------------------------------------
# Слой агрегации по месяцам
# ---------------------------------------------------------------------------


def _aggregate_cash_flow(
    transactions: list[Transaction],
    period: MonthlyPeriod,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """
    Возвращает (inflow, outflow, transfer_in, transfer_out) за период.
    Работает только с TransactionType.INCOME / EXPENSE / TRANSFER.
    """
    inflow = ZERO
    outflow = ZERO
    transfer_in = ZERO
    transfer_out = ZERO

    p_start = period.first_day
    p_end = period.last_day

    for txn in transactions:
        if not (p_start <= txn.payment_date <= p_end):
            continue
        amount = txn.amount_base_currency
        if txn.transaction_type == TransactionType.INCOME:
            inflow += amount
        elif txn.transaction_type == TransactionType.EXPENSE:
            outflow += amount
        elif txn.transaction_type == TransactionType.TRANSFER:
            transfer_out += amount
            transfer_in += amount   # Переводы нейтральны: обе стороны в одной компании

    return (
        _round(inflow),
        _round(outflow),
        _round(transfer_in),
        _round(transfer_out),
    )


def _aggregate_pnl(
    transactions: list[Transaction],
    categories_map: dict[UUID, Category],
    period: MonthlyPeriod,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """
    Возвращает (revenue, cogs, operating_expenses, depreciation, interest_expense).
    Классификация расходов — по тегу категории `pnl_line`:
      "cogs", "opex", "depreciation", "interest", "payroll"
    """
    revenue = ZERO
    cogs = ZERO
    opex = ZERO
    depreciation = ZERO
    interest_expense = ZERO
    payroll = ZERO

    p_start = period.first_day
    p_end = period.last_day

    for txn in transactions:
        accrual = txn.accrual_date
        if accrual is None or not (p_start <= accrual <= p_end):
            continue
        if txn.transaction_type == TransactionType.TRANSFER:
            continue

        amount = txn.amount_base_currency
        pnl_line: str = "other"

        if txn.category_id and txn.category_id in categories_map:
            cat = categories_map[txn.category_id]
            pnl_line = (cat.icon or "other").lower()

        if txn.transaction_type == TransactionType.INCOME:
            revenue += amount
        elif txn.transaction_type == TransactionType.EXPENSE:
            if pnl_line == "cogs":
                cogs += amount
            elif pnl_line == "depreciation":
                depreciation += amount
            elif pnl_line == "interest":
                interest_expense += amount
            elif pnl_line == "payroll":
                payroll += amount
            else:
                opex += amount

    return (
        _round(revenue),
        _round(cogs),
        _round(opex),
        _round(depreciation),
        _round(interest_expense),
    )


# ---------------------------------------------------------------------------
# Специализированные движки по бизнес-моделям
# ---------------------------------------------------------------------------


def _enrich_saas_metrics(
    transactions: list[Transaction],
    categories_map: dict[UUID, Category],
    period: MonthlyPeriod,
    prev_mrr: Decimal,
) -> SaaSMetrics:
    """
    Рассчитывает SaaS-метрики за месяц: MRR, ARR, Churn Rate.

    Предполагает, что категории со slug "subscription_revenue" — это MRR.
    Churn — категории с тегом "churned_subscription".
    """
    metrics = SaaSMetrics(period=period)
    p_start = period.first_day
    p_end = period.last_day

    new_mrr = ZERO
    churned_mrr = ZERO
    expansion_mrr = ZERO

    for txn in transactions:
        if not (p_start <= txn.payment_date <= p_end):
            continue
        if txn.transaction_type != TransactionType.INCOME:
            continue

        cat_icon = ""
        if txn.category_id and txn.category_id in categories_map:
            cat_icon = (categories_map[txn.category_id].icon or "").lower()

        amount = txn.amount_base_currency
        tags = [str(t).lower() for t in txn.tags]

        if "churned_subscription" in tags:
            churned_mrr += amount
        elif "expansion_subscription" in tags:
            expansion_mrr += amount
        elif cat_icon == "subscription_revenue" or "new_subscription" in tags:
            new_mrr += amount

    mrr = _round(max(ZERO, prev_mrr + new_mrr + expansion_mrr - churned_mrr))
    churn_rate = (
        _round(churned_mrr / prev_mrr) if prev_mrr > ZERO else ZERO
    )

    metrics.new_mrr = new_mrr
    metrics.churned_mrr = churned_mrr
    metrics.expansion_mrr = expansion_mrr
    metrics.mrr = mrr
    metrics.arr = _round(mrr * Decimal("12"))
    metrics.churn_rate = churn_rate
    return metrics


def _enrich_milestone_metrics(
    transactions: list[Transaction],
    period: MonthlyPeriod,
) -> MilestoneMetrics:
    """
    Рассчитывает метрики проектных этапов.
    Начисленные суммы (accrual_date) — invoiced.
    Оплаченные суммы (payment_date) — collected.
    """
    metrics = MilestoneMetrics(period=period)
    p_start = period.first_day
    p_end = period.last_day

    for txn in transactions:
        if txn.transaction_type != TransactionType.INCOME:
            continue

        # Начислено по вехе
        if txn.accrual_date and p_start <= txn.accrual_date <= p_end:
            metrics.invoiced += txn.amount_base_currency
            tags = [str(t).lower() for t in txn.tags]
            if "milestone_completed" in tags:
                metrics.milestone_count_completed += 1

        # Фактически получено
        if p_start <= txn.payment_date <= p_end:
            metrics.collected += txn.amount_base_currency

    metrics.invoiced = _round(metrics.invoiced)
    metrics.collected = _round(metrics.collected)
    metrics.backlog = _round(max(ZERO, metrics.invoiced - metrics.collected))
    return metrics


# ---------------------------------------------------------------------------
# Главная функция расчёта
# ---------------------------------------------------------------------------


async def calculate_financials(
    company_id: UUID,
    start_date: date,
    end_date: date,
    db: AsyncSession,
    company_settings: Optional[dict] = None,
) -> FinancialReport:
    """
    Главная точка входа финансового движка.

    Агрегирует ДДС (Cash Flow), P&L и 3-Way Balance Sheet
    за указанный период (помесячно).

    Параметры company_settings читаются из Company.settings JSONB:
      - "tax_regime":     "usn_income" | "usn_income_minus_expenses" | "osno"
      - "business_model": "standard" | "saas" | "milestone"
      - "currency":       "RUB" | "USD" | ...
    """
    if company_settings is None:
        company_settings = {}

    tax_regime = _get_tax_regime(company_settings)
    business_model = _get_business_model(company_settings)
    currency = company_settings.get("currency", "RUB")

    tax_calc = TaxCalculator(tax_regime)
    periods = _build_periods(start_date, end_date)

    # Подгружаем все транзакции за период одним запросом каждого типа
    all_cash_txns = await _fetch_transactions_by_payment_date(
        db, company_id, start_date, end_date
    )
    all_accrual_txns = await _fetch_transactions_by_accrual_date(
        db, company_id, start_date, end_date
    )
    categories_map = await _fetch_categories_map(db, company_id)

    # Одобренные заявки на оплату — прогноз расходов для будущих периодов
    all_approved_requests = await _fetch_approved_payment_requests(
        db, company_id, start_date, end_date
    )

    report = FinancialReport(
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        business_model=business_model,
        tax_regime=tax_regime,
        currency=currency,
    )

    # Баланс на начало первого месяца (день до start_date)
    prev_closing_balance = await _fetch_account_balances(
        db,
        company_id,
        as_of_date=date(start_date.year, start_date.month, 1)
        if start_date.day == 1
        else start_date,
    )
    prev_mrr = ZERO
    cumulative_tax_payable = ZERO
    cumulative_net_profit = ZERO

    # Итоговые накопители
    total_revenue = ZERO
    total_inflow = ZERO
    total_outflow = ZERO
    total_net_profit = ZERO
    total_tax = ZERO

    for period in periods:
        # ---------------------------------------------------------------
        # 1. ДДС (Cash Flow) — по payment_date
        # ---------------------------------------------------------------
        inflow, outflow, transfer_in, transfer_out = _aggregate_cash_flow(
            all_cash_txns, period
        )

        # Одобренные заявки на оплату: суммируем по planned_date внутри периода.
        # Учитываются только заявки, ещё не конвертированные в транзакцию
        # (paid_transaction_id IS NULL, иначе они уже войдут в outflow выше).
        approved_outflow = _round(sum(
            (req.amount for req in all_approved_requests
             if period.first_day <= req.planned_date <= period.last_day),
            ZERO,
        ))

        # NCF и остаток включают прогнозные расходы из заявок
        ncf = _round(inflow - outflow - approved_outflow)
        opening_balance = prev_closing_balance
        closing_balance = _round(opening_balance + ncf)
        cash_gap = closing_balance if closing_balance < ZERO else ZERO

        cf_month = CashFlowMonth(
            period=period,
            inflow=inflow,
            outflow=outflow,
            net_cash_flow=ncf,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            cash_gap=cash_gap,
            transfer_in=transfer_in,
            transfer_out=transfer_out,
            approved_requests_outflow=approved_outflow,
        )
        report.cash_flow.append(cf_month)
        prev_closing_balance = closing_balance

        # ---------------------------------------------------------------
        # 2. P&L — по accrual_date
        # ---------------------------------------------------------------
        revenue, cogs, opex, depreciation, interest_expense = _aggregate_pnl(
            all_accrual_txns, categories_map, period
        )

        # ФОТ — расходы с тегом "payroll" или категорией "payroll"
        payroll_gross = ZERO
        for txn in all_accrual_txns:
            if txn.accrual_date is None:
                continue
            if not (period.first_day <= txn.accrual_date <= period.last_day):
                continue
            tags = [str(t).lower() for t in txn.tags]
            cat_icon = ""
            if txn.category_id and txn.category_id in categories_map:
                cat_icon = (categories_map[txn.category_id].icon or "").lower()
            if "payroll" in tags or cat_icon == "payroll":
                payroll_gross += txn.amount_base_currency
        payroll_gross = _round(payroll_gross)

        payroll_result = calculate_payroll(payroll_gross)

        gross_profit = _round(revenue - cogs)
        ebitda = _round(gross_profit - opex - payroll_result.total_employer_cost)
        ebit = _round(ebitda - depreciation)
        ebt = _round(ebit - interest_expense)
        tax_amount, _ = tax_calc.calculate(
            revenue=revenue,
            expenses=cogs + opex,
            payroll_total=payroll_result.total_employer_cost,
        )
        net_profit = _round(ebt - tax_amount)

        pnl_month = PnLMonth(
            period=period,
            revenue=revenue,
            cost_of_goods=cogs,
            gross_profit=gross_profit,
            operating_expenses=opex,
            ebitda=ebitda,
            depreciation=depreciation,
            ebit=ebit,
            interest_expense=interest_expense,
            ebt=ebt,
            tax=tax_amount,
            net_profit=net_profit,
            payroll_gross=payroll_result.gross,
            payroll_insurance=payroll_result.insurance,
            payroll_total=payroll_result.total_employer_cost,
        )
        report.pnl.append(pnl_month)

        cumulative_tax_payable += tax_amount
        cumulative_net_profit += net_profit

        # ---------------------------------------------------------------
        # 3. 3-Way Balance Sheet
        # ---------------------------------------------------------------
        # Дебиторка = начисленная выручка − фактические поступления
        accounts_receivable = _round(max(ZERO, revenue - inflow))
        # Кредиторка = фактические оттоки − начисленные расходы
        total_expenses_accrual = _round(cogs + opex + payroll_result.total_employer_cost)
        accounts_payable = _round(max(ZERO, total_expenses_accrual - outflow))

        total_assets = _round(closing_balance + accounts_receivable)
        total_liabilities = _round(accounts_payable + cumulative_tax_payable)
        equity = _round(total_assets - total_liabilities)

        balance = BalanceSheet(
            period=period,
            cash_and_equivalents=closing_balance,
            accounts_receivable=accounts_receivable,
            total_assets=total_assets,
            accounts_payable=accounts_payable,
            tax_payable=cumulative_tax_payable,
            total_liabilities=total_liabilities,
            equity=equity,
        )
        report.balance.append(balance)

        # ---------------------------------------------------------------
        # 4. Метрики по бизнес-модели
        # ---------------------------------------------------------------
        if business_model == BusinessModel.SAAS:
            saas = _enrich_saas_metrics(
                all_cash_txns, categories_map, period, prev_mrr
            )
            report.saas_metrics.append(saas)
            prev_mrr = saas.mrr

        elif business_model == BusinessModel.MILESTONE:
            milestone = _enrich_milestone_metrics(all_cash_txns, period)
            report.milestone_metrics.append(milestone)

        # Накопители итогов
        total_revenue += revenue
        total_inflow += inflow
        total_outflow += outflow
        total_net_profit += net_profit
        total_tax += tax_amount

    # -----------------------------------------------------------------------
    # Итоговые показатели за весь период
    # -----------------------------------------------------------------------
    report.totals = {
        "total_revenue": _round(total_revenue),
        "total_inflow": _round(total_inflow),
        "total_outflow": _round(total_outflow),
        "net_cash_flow": _round(total_inflow - total_outflow),
        "total_net_profit": _round(total_net_profit),
        "total_tax": _round(total_tax),
        "final_balance": prev_closing_balance,
        "periods_count": len(periods),
    }

    return report
