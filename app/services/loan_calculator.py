"""
Сервис: Кредитный калькулятор.

Реализует:
  1. generate_schedule()    — расчёт графика платежей (аннуитет / дифференцированный)
  2. create_loan_schedule() — сохранение графика в БД при открытии кредита
  3. split_loan_payment()   — «расщепление» платежа на тело + проценты,
                              закрытие строки графика и обновление остатка долга
"""
from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.assets_loans import Loan, LoanPaymentSchedule, LoanType

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _add_months(d: date, months: int) -> date:
    """Прибавляет целое число месяцев к дате, не выходя за последний день месяца."""
    month = d.month - 1 + months
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ─────────────────────────────────────────────────────────────────────────────
# СТРУКТУРЫ РЕЗУЛЬТАТА
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PaymentRow:
    """Одна строка планового графика платежей."""
    payment_number:    int
    payment_date:      date
    principal_amount:  Decimal   # тело кредита в этом периоде
    interest_amount:   Decimal   # проценты в этом периоде
    total_payment:     Decimal   # principal + interest
    remaining_principal: Decimal  # остаток долга ПОСЛЕ этого платежа


@dataclass
class PaymentSplit:
    """Результат расщепления фактического платежа."""
    schedule_id:         UUID
    principal_amount:    Decimal
    interest_amount:     Decimal
    total_payment:       Decimal
    remaining_principal: Decimal   # остаток долга после платежа
    loan_fully_paid:     bool       # True — кредит полностью погашен


# ─────────────────────────────────────────────────────────────────────────────
# РАСЧЁТ ГРАФИКА (чистые функции — без БД)
# ─────────────────────────────────────────────────────────────────────────────


def generate_annuity_schedule(
    total_amount:  Decimal,
    interest_rate: Decimal,   # годовая ставка, % (например 18.0)
    start_date:    date,      # дата выдачи (первый платёж через 1 месяц)
    term_months:   int,
) -> list[PaymentRow]:
    """
    Аннуитетный график: одинаковый ежемесячный платёж PMT.

    PMT = P × r × (1+r)^n  /  ((1+r)^n − 1)
    где r = годовая_ставка / 100 / 12, n = срок_в_месяцах.

    Крайний случай r=0: PMT = total_amount / term_months.
    Последний платёж корректируется на копейки округления.
    """
    if term_months <= 0:
        raise ValueError("term_months должен быть > 0")

    monthly_rate = interest_rate / Decimal("100") / Decimal("12")
    rows: list[PaymentRow] = []
    remaining = total_amount

    if monthly_rate == ZERO:
        # Беспроцентный займ
        base_principal = _q(total_amount / Decimal(term_months))
        for i in range(1, term_months + 1):
            if i == term_months:
                principal = remaining
            else:
                principal = base_principal
            remaining = _q(remaining - principal)
            rows.append(PaymentRow(
                payment_number=i,
                payment_date=_add_months(start_date, i),
                principal_amount=principal,
                interest_amount=ZERO,
                total_payment=principal,
                remaining_principal=remaining,
            ))
        return rows

    # Стандартный аннуитет
    factor = (Decimal("1") + monthly_rate) ** term_months
    pmt = _q(total_amount * monthly_rate * factor / (factor - Decimal("1")))

    for i in range(1, term_months + 1):
        interest  = _q(remaining * monthly_rate)
        principal = pmt - interest

        # Корректировка последнего платежа на накопленное округление
        if i == term_months:
            principal = remaining
            pmt_adjusted = _q(principal + interest)
        else:
            pmt_adjusted = pmt

        remaining = _q(remaining - principal)
        if remaining < ZERO:    # защита от отрицательных остатков при округлении
            principal += remaining
            remaining  = ZERO

        rows.append(PaymentRow(
            payment_number=i,
            payment_date=_add_months(start_date, i),
            principal_amount=_q(principal),
            interest_amount=_q(interest),
            total_payment=_q(pmt_adjusted),
            remaining_principal=remaining,
        ))

    return rows


def generate_differentiated_schedule(
    total_amount:  Decimal,
    interest_rate: Decimal,
    start_date:    date,
    term_months:   int,
) -> list[PaymentRow]:
    """
    Дифференцированный график: фиксированное тело кредита каждый месяц,
    убывающие проценты.

    Тело = total_amount / term_months (одинаково).
    Проценты = остаток_долга × monthly_rate (убывают).
    Последний платёж корректируется на копейки округления.
    """
    if term_months <= 0:
        raise ValueError("term_months должен быть > 0")

    monthly_rate     = interest_rate / Decimal("100") / Decimal("12")
    base_principal   = _q(total_amount / Decimal(term_months))
    remaining        = total_amount
    rows: list[PaymentRow] = []

    for i in range(1, term_months + 1):
        interest = _q(remaining * monthly_rate)

        # Последний платёж — весь остаток тела
        principal = remaining if i == term_months else base_principal
        remaining = _q(remaining - principal)
        if remaining < ZERO:
            principal += remaining
            remaining  = ZERO

        rows.append(PaymentRow(
            payment_number=i,
            payment_date=_add_months(start_date, i),
            principal_amount=_q(principal),
            interest_amount=_q(interest),
            total_payment=_q(principal + interest),
            remaining_principal=remaining,
        ))

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# РАБОТА С БАЗОЙ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────


async def create_loan_schedule(
    db:   AsyncSession,
    loan: Loan,
) -> list[LoanPaymentSchedule]:
    """
    Генерирует и сохраняет полный плановый график для нового кредита.

    Вызывается сразу при создании Loan. Возвращает список записей графика.
    """
    if loan.loan_type == LoanType.ANNUITY:
        rows = generate_annuity_schedule(
            total_amount=loan.total_amount,
            interest_rate=loan.interest_rate,
            start_date=loan.start_date,
            term_months=loan.term_months,
        )
    else:
        rows = generate_differentiated_schedule(
            total_amount=loan.total_amount,
            interest_rate=loan.interest_rate,
            start_date=loan.start_date,
            term_months=loan.term_months,
        )

    schedule_objs: list[LoanPaymentSchedule] = []
    for row in rows:
        entry = LoanPaymentSchedule(
            id=uuid.uuid4(),
            loan_id=loan.id,
            payment_date=row.payment_date,
            principal_amount=row.principal_amount,
            interest_amount=row.interest_amount,
            total_payment=row.total_payment,
            is_paid=False,
        )
        db.add(entry)
        schedule_objs.append(entry)

    await db.flush()
    return schedule_objs


async def split_loan_payment(
    db:             AsyncSession,
    loan_id:        UUID,
    transaction_id: UUID,
    payment_amount: Decimal,    # фактически уплаченная сумма (для проверки)
    paid_at:        Optional[datetime] = None,
) -> PaymentSplit:
    """
    «Расщепляет» фактический платёж на тело + проценты.

    Алгоритм:
      1. Находит первую неоплаченную строку графика для этого кредита
         (сортировка по payment_date ASC).
      2. Закрывает её: is_paid=True, transaction_id=..., paid_at=now().
      3. Уменьшает loan.remaining_principal на principal_amount.
      4. Если remaining_principal ≤ 0 → кредит погашен (is_active=False).

    Примечание: Сервис использует суммы ИЗ ГРАФИКА (не из фактического
    платежа), т.к. в российском учёте принято «закрывать период по плану».
    Отклонение от плана (досрочное погашение) обрабатывается отдельно.

    Raises:
        ValueError: если для кредита нет неоплаченных строк в графике.
    """
    # Загружаем кредит
    loan_result = await db.execute(select(Loan).where(Loan.id == loan_id))
    loan: Optional[Loan] = loan_result.scalar_one_or_none()
    if loan is None:
        raise ValueError(f"Кредит {loan_id} не найден.")

    # Ищем первую неоплаченную строку
    sched_result = await db.execute(
        select(LoanPaymentSchedule).where(
            and_(
                LoanPaymentSchedule.loan_id == loan_id,
                LoanPaymentSchedule.is_paid.is_(False),
            )
        ).order_by(LoanPaymentSchedule.payment_date).limit(1)
    )
    entry: Optional[LoanPaymentSchedule] = sched_result.scalar_one_or_none()

    if entry is None:
        raise ValueError(
            f"Кредит {loan_id}: все периоды в графике уже оплачены."
        )

    # Закрываем строку графика
    now = paid_at or datetime.now(tz=timezone.utc)
    entry.is_paid        = True
    entry.paid_at        = now
    entry.transaction_id = transaction_id

    # Обновляем остаток долга
    new_remaining = _q(loan.remaining_principal - entry.principal_amount)
    if new_remaining < ZERO:
        new_remaining = ZERO
    loan.remaining_principal = new_remaining

    # Если долг полностью погашен — деактивируем кредит
    fully_paid = new_remaining == ZERO or (
        await _all_schedule_paid(db, loan_id)
    )
    if fully_paid:
        loan.is_active = False

    await db.flush()

    return PaymentSplit(
        schedule_id=entry.id,
        principal_amount=entry.principal_amount,
        interest_amount=entry.interest_amount,
        total_payment=entry.total_payment,
        remaining_principal=new_remaining,
        loan_fully_paid=fully_paid,
    )


async def _all_schedule_paid(db: AsyncSession, loan_id: UUID) -> bool:
    """True если все строки графика помечены как оплаченные."""
    result = await db.execute(
        select(LoanPaymentSchedule).where(
            and_(
                LoanPaymentSchedule.loan_id == loan_id,
                LoanPaymentSchedule.is_paid.is_(False),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none() is None


# ─────────────────────────────────────────────────────────────────────────────
# СВОДКА КРЕДИТА (для карточки кредита)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LoanSummary:
    loan_id:             UUID
    total_amount:        Decimal
    paid_principal:      Decimal
    remaining_principal: Decimal
    total_interest_paid: Decimal   # сумма процентов по оплаченным периодам
    total_interest_plan: Decimal   # переплата по всему графику
    next_payment_date:   Optional[date]
    next_payment_amount: Optional[Decimal]
    periods_paid:        int
    periods_total:       int


async def get_loan_summary(
    db:      AsyncSession,
    loan_id: UUID,
) -> LoanSummary:
    """Возвращает сводную аналитику по кредиту."""
    loan_result = await db.execute(select(Loan).where(Loan.id == loan_id))
    loan: Optional[Loan] = loan_result.scalar_one_or_none()
    if loan is None:
        raise ValueError(f"Кредит {loan_id} не найден.")

    sched_result = await db.execute(
        select(LoanPaymentSchedule)
        .where(LoanPaymentSchedule.loan_id == loan_id)
        .order_by(LoanPaymentSchedule.payment_date)
    )
    rows = list(sched_result.scalars().all())

    paid_rows   = [r for r in rows if r.is_paid]
    unpaid_rows = [r for r in rows if not r.is_paid]

    next_row = unpaid_rows[0] if unpaid_rows else None

    return LoanSummary(
        loan_id=loan_id,
        total_amount=loan.total_amount,
        paid_principal=loan.paid_principal,
        remaining_principal=loan.remaining_principal,
        total_interest_paid=_q(sum((r.interest_amount for r in paid_rows), ZERO)),
        total_interest_plan=_q(sum((r.interest_amount for r in rows), ZERO)),
        next_payment_date=next_row.payment_date if next_row else None,
        next_payment_amount=next_row.total_payment if next_row else None,
        periods_paid=len(paid_rows),
        periods_total=len(rows),
    )
