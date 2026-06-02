"""
Сервис: Зарплатный конвейер ФОТ (Спринт 10).

Жизненный цикл расчётного листка:
  1. create_payroll_draft()     → status = DRAFT  (создание/обновление черновика)
  2. approve_payroll_calculation() → status = ACCRUED (утверждение к выплате)
  3. link_payment_to_payroll()  → paid_amount += N → при paid >= total: PAID

IDOR-защита: каждая функция проверяет company_id принадлежность
всех загружаемых объектов.

UPSERT в create_payroll_draft:
  При повторном вызове за тот же месяц — обновляет черновик (не дублирует).
  Если листок уже ACCRUED или PAID — выбрасывает ошибку.

Транзакционный остаток (свободный баланс транзакции):
  Перед привязкой выплаты проверяем, что сумма не превышает
  transaction.amount_base_currency минус уже привязанные суммы
  через TransactionAccrualLink (учёт из Sprint 9).
  Для платежей ФОТ отдельной link-таблицы нет — изменение paid_amount
  атомарно через flush+recalc.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.accruals import TransactionAccrualLink
from app.domain.models.finance import Transaction
from app.domain.models.payroll import Employee, PayrollCalculation, PayrollStatus

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _first_of_month(d: date) -> date:
    """Нормализует дату до 1-го числа месяца (2026-05-15 → 2026-05-01)."""
    return d.replace(day=1)


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗУЛЬТАТЫ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PayrollDraftResult:
    """Итог создания/обновления расчётного листка."""
    calc_id:       UUID
    employee_id:   UUID
    month_date:    date
    total_accrued: Decimal
    status:        PayrollStatus
    is_new:        bool             # True = создан новый, False = обновлён черновик


@dataclass
class PaymentLinkResult:
    """Итог привязки выплаты к расчётному листку."""
    calc_id:          UUID
    new_paid_amount:  Decimal
    outstanding:      Decimal        # остаток долга после выплаты
    new_status:       PayrollStatus
    txn_remaining:    Decimal        # свободный остаток транзакции после выплаты


@dataclass
class PayrollMonthSummary:
    """Сводка ФОТ за месяц по всему персоналу компании."""
    month_date:      date
    employee_count:  int             # сотрудников в листках за месяц
    total_accrued:   Decimal         # итого начислено
    total_paid:      Decimal         # итого выплачено
    total_outstanding: Decimal       # итого долг перед персоналом
    draft_count:     int             # листков в статусе DRAFT
    accrued_count:   int             # листков в статусе ACCRUED
    paid_count:      int             # листков в статусе PAID


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _load_employee_or_404(
    db:          AsyncSession,
    employee_id: UUID,
    company_id:  UUID,
) -> Employee:
    """Загружает Employee с IDOR-проверкой company_id."""
    result = await db.execute(
        select(Employee).where(
            and_(
                Employee.id == employee_id,
                Employee.company_id == company_id,
                Employee.is_deleted.is_(False),
            )
        )
    )
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Сотрудник {employee_id} не найден в компании {company_id}.",
        )
    return emp


async def _load_calc_or_404(
    db:         AsyncSession,
    calc_id:    UUID,
    company_id: UUID,
) -> PayrollCalculation:
    """Загружает PayrollCalculation с IDOR-проверкой company_id."""
    result = await db.execute(
        select(PayrollCalculation).where(
            and_(
                PayrollCalculation.id == calc_id,
                PayrollCalculation.company_id == company_id,
            )
        )
    )
    calc = result.scalar_one_or_none()
    if not calc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Расчётный листок {calc_id} не найден.",
        )
    return calc


async def _load_txn_or_404(
    db:             AsyncSession,
    transaction_id: UUID,
    company_id:     UUID,
) -> Transaction:
    """Загружает Transaction с IDOR-проверкой company_id."""
    result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.id == transaction_id,
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Транзакция {transaction_id} не найдена в компании.",
        )
    return txn


async def _txn_free_amount(
    db:             AsyncSession,
    transaction_id: UUID,
    txn_total:      Decimal,
) -> Decimal:
    """
    Свободный остаток транзакции = total − уже привязанные суммы
    через TransactionAccrualLink (Sprint 9).

    Для платежей ФОТ отдельная link-таблица не создавалась в этом спринте,
    поэтому учитываем только accrual-привязки. Если в будущем добавится
    PayrollPaymentLink — сюда добавить второй SELECT.
    """
    result = await db.execute(
        select(func.coalesce(func.sum(TransactionAccrualLink.linked_amount), 0))
        .where(TransactionAccrualLink.transaction_id == transaction_id)
    )
    already_linked = _q(Decimal(str(result.scalar() or "0")))
    return _q(txn_total - already_linked)


# ─────────────────────────────────────────────────────────────────────────────
# ШАГИ КОНВЕЙЕРА
# ─────────────────────────────────────────────────────────────────────────────


async def create_payroll_draft(
    db:             AsyncSession,
    company_id:     UUID,
    employee_id:    UUID,
    month_date:     date,
    accrued_salary: Decimal,
    accrued_bonus:  Decimal = ZERO,
    notes:          Optional[str] = None,
) -> PayrollDraftResult:
    """
    Создаёт или обновляет черновик расчётного листка за месяц.

    UPSERT-логика:
      - Если листок не существует → создаётся новый со статусом DRAFT.
      - Если листок существует в статусе DRAFT → данные обновляются.
      - Если листок ACCRUED или PAID → HTTP 409: нельзя перезаписать утверждённый.

    month_date нормализуется до 1-го числа месяца автоматически.

    Raises:
        HTTPException 404 — сотрудник не найден / не принадлежит компании.
        HTTPException 422 — отрицательные суммы.
        HTTPException 409 — листок уже утверждён или выплачен.
    """
    if accrued_salary < ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Оклад не может быть отрицательным.",
        )
    if accrued_bonus < ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Премия не может быть отрицательной.",
        )

    # IDOR: убеждаемся что сотрудник принадлежит компании
    await _load_employee_or_404(db, employee_id, company_id)

    month = _first_of_month(month_date)
    total = _q(accrued_salary + accrued_bonus)

    # Проверяем наличие существующего листка
    existing_result = await db.execute(
        select(PayrollCalculation).where(
            and_(
                PayrollCalculation.company_id  == company_id,
                PayrollCalculation.employee_id == employee_id,
                PayrollCalculation.month_date  == month,
            )
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        # Нельзя перезаписать утверждённый или выплаченный листок
        if existing.status in (PayrollStatus.ACCRUED, PayrollStatus.PAID):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Расчётный листок за {month.strftime('%B %Y')} уже "
                    f"имеет статус '{existing.status.value}' и не может быть изменён. "
                    "Для корректировки создайте новый листок на следующий месяц."
                ),
            )
        # Обновляем черновик
        existing.accrued_salary = _q(accrued_salary)
        existing.accrued_bonus  = _q(accrued_bonus)
        existing.total_accrued  = total
        if notes is not None:
            existing.notes = notes
        await db.flush()
        return PayrollDraftResult(
            calc_id=existing.id,
            employee_id=employee_id,
            month_date=month,
            total_accrued=total,
            status=PayrollStatus.DRAFT,
            is_new=False,
        )

    # Создаём новый черновик
    calc = PayrollCalculation(
        id=uuid.uuid4(),
        company_id=company_id,
        employee_id=employee_id,
        month_date=month,
        accrued_salary=_q(accrued_salary),
        accrued_bonus=_q(accrued_bonus),
        total_accrued=total,
        paid_amount=ZERO,
        status=PayrollStatus.DRAFT,
        notes=notes,
    )
    db.add(calc)
    await db.flush()

    return PayrollDraftResult(
        calc_id=calc.id,
        employee_id=employee_id,
        month_date=month,
        total_accrued=total,
        status=PayrollStatus.DRAFT,
        is_new=True,
    )


async def approve_payroll_calculation(
    db:         AsyncSession,
    company_id: UUID,
    calc_id:    UUID,
) -> PayrollCalculation:
    """
    Утверждает расчётный листок: DRAFT → ACCRUED.

    После утверждения сумма зафиксирована и не может быть изменена
    через create_payroll_draft. Доступна выплата через link_payment_to_payroll.

    Raises:
        HTTPException 404 — листок не найден.
        HTTPException 422 — листок не в статусе DRAFT.
    """
    calc = await _load_calc_or_404(db, calc_id, company_id)

    if calc.status != PayrollStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Утвердить можно только черновик. "
                f"Текущий статус: '{calc.status.value}'."
            ),
        )

    calc.status = PayrollStatus.ACCRUED
    await db.flush()
    return calc


async def link_payment_to_payroll(
    db:             AsyncSession,
    company_id:     UUID,
    transaction_id: UUID,
    calc_id:        UUID,
    amount:         Decimal,
) -> PaymentLinkResult:
    """
    Регистрирует выплату зарплаты из транзакции ДДС в расчётный листок.

    Проверки:
      1. amount > 0
      2. Листок принадлежит компании (IDOR) и в статусе ACCRUED
      3. amount ≤ outstanding листка (не переплачиваем сотруднику)
      4. amount ≤ free balance транзакции (не тратим больше, чем есть)

    После записи:
      paid_amount += amount
      Если paid_amount >= total_accrued → status = PAID

    Raises:
        HTTPException 404  — листок или транзакция не найдены.
        HTTPException 422  — нарушение любой из проверок.
    """
    amount = _q(amount)
    if amount <= ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Сумма выплаты должна быть больше нуля.",
        )

    calc = await _load_calc_or_404(db, calc_id, company_id)
    txn  = await _load_txn_or_404(db, transaction_id, company_id)

    # Листок должен быть утверждён перед выплатой
    if calc.status == PayrollStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нельзя привязать выплату к черновику. Сначала утвердите листок.",
        )
    if calc.status == PayrollStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Расчётный листок уже полностью выплачен.",
        )

    # Проверяем остаток долга перед сотрудником
    outstanding = calc.outstanding
    if amount > outstanding:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма выплаты {amount} превышает остаток долга "
                f"перед сотрудником {outstanding} "
                f"(начислено {calc.total_accrued}, выплачено {calc.paid_amount})."
            ),
        )

    # Проверяем свободный остаток транзакции
    txn_free = await _txn_free_amount(db, transaction_id, txn.amount_base_currency)
    if amount > txn_free:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма выплаты {amount} превышает свободный остаток "
                f"транзакции {txn_free} "
                f"(транзакция на {txn.amount_base_currency})."
            ),
        )

    # Регистрируем выплату
    calc.paid_amount = _q(calc.paid_amount + amount)
    if calc.paid_amount >= calc.total_accrued:
        calc.status = PayrollStatus.PAID

    await db.flush()

    return PaymentLinkResult(
        calc_id=calc.id,
        new_paid_amount=calc.paid_amount,
        outstanding=calc.outstanding,
        new_status=calc.status,
        txn_remaining=_q(txn_free - amount),
    )


# ─────────────────────────────────────────────────────────────────────────────
# АНАЛИТИКА
# ─────────────────────────────────────────────────────────────────────────────


async def get_payroll_debts_summary(
    db:         AsyncSession,
    company_id: UUID,
    month_date: date,
) -> PayrollMonthSummary:
    """
    Сводка по ФОТ за конкретный месяц: начислено, выплачено, долг.

    Агрегирует все PayrollCalculation компании за указанный месяц.
    month_date нормализуется до 1-го числа.
    """
    month = _first_of_month(month_date)

    result = await db.execute(
        select(
            func.count(PayrollCalculation.id).label("count"),
            func.coalesce(func.sum(PayrollCalculation.total_accrued), 0).label("total_accrued"),
            func.coalesce(func.sum(PayrollCalculation.paid_amount),   0).label("total_paid"),
            func.count(
                PayrollCalculation.id
            ).filter(PayrollCalculation.status == PayrollStatus.DRAFT).label("draft_count"),
            func.count(
                PayrollCalculation.id
            ).filter(PayrollCalculation.status == PayrollStatus.ACCRUED).label("accrued_count"),
            func.count(
                PayrollCalculation.id
            ).filter(PayrollCalculation.status == PayrollStatus.PAID).label("paid_count"),
        )
        .where(
            and_(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.month_date == month,
            )
        )
    )
    row = result.one()

    total_accrued   = _q(Decimal(str(row.total_accrued)))
    total_paid      = _q(Decimal(str(row.total_paid)))
    total_outstanding = _q(max(ZERO, total_accrued - total_paid))

    return PayrollMonthSummary(
        month_date=month,
        employee_count=row.count,
        total_accrued=total_accrued,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
        draft_count=row.draft_count,
        accrued_count=row.accrued_count,
        paid_count=row.paid_count,
    )


async def get_payroll_by_employee(
    db:          AsyncSession,
    company_id:  UUID,
    employee_id: UUID,
    months:      int = 12,
) -> list[PayrollCalculation]:
    """
    История расчётных листков сотрудника за последние N месяцев.
    Используется для карточки сотрудника — аналитика выплат.
    """
    # IDOR: проверяем принадлежность сотрудника
    await _load_employee_or_404(db, employee_id, company_id)

    result = await db.execute(
        select(PayrollCalculation)
        .where(
            and_(
                PayrollCalculation.company_id  == company_id,
                PayrollCalculation.employee_id == employee_id,
            )
        )
        .order_by(PayrollCalculation.month_date.desc())
        .limit(months)
    )
    return list(result.scalars().all())
