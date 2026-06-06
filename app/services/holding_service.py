"""
Сервис: Консолидация холдинга и элиминация ВГО (Спринт 11).

Внутригрупповые операции (ВГО) — движение денег/документов между своими юрлицами.
При консолидации холдинговой отчётности ВГО исключаются во избежание двойного учёта:
  • ООО «Альфа» продала ООО «Бета» на 1 млн → у Альфы выручка, у Беты затраты
  • В консолидированном P&L обе строки исключаются (net effect = 0)
  • В консолидированном ДДС аналогично исключается перевод внутри группы

Жизненный цикл:
  1. create_intra_group_*_link() — связывает пары операций, выставляет is_intra_group=True
  2. get_consolidated_dds_totals() — суммы ДДС: грязный vs. очищенный от ВГО
  3. get_consolidated_pnl_totals() — суммы P&L по начислениям: грязный vs. очищенный

Защита от дублирования:
  Перед созданием IntraGroupLink проверяем наличие существующей записи с теми же парами,
  и в прямом, и в обратном порядке (A→B ≡ B→A для целей дедуплицирования).

Точность: Decimal(15,2) HALF_UP через _q() на всех расчётных значениях.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.accruals import AccrualDocument, AccrualDocumentType
from app.domain.models.finance import Transaction, TransactionType
from app.domain.models.holdings import IntraGroupLink

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _q(v: Decimal) -> Decimal:
    """Округление до копейки HALF_UP."""
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗУЛЬТАТЫ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IntraGroupLinkResult:
    """Итог создания ВГО-связи."""
    link_id:            UUID
    source_id:          UUID
    destination_id:     UUID
    linked_amount:      Decimal
    link_type:          str     # "transaction" | "accrual"


@dataclass
class ConsolidatedDDS:
    """Консолидированный отчёт о движении денежных средств холдинга."""
    company_ids:         list[UUID]
    start_date:          date
    end_date:            date
    # ── Грязный оборот (всё, включая ВГО) ───────────────────────────────────
    dirty_income:        Decimal
    dirty_expense:       Decimal
    dirty_net:           Decimal
    # ── Консолидированный (без ВГО) ─────────────────────────────────────────
    clean_income:        Decimal
    clean_expense:       Decimal
    clean_net:           Decimal
    # ── Исключённые ВГО ─────────────────────────────────────────────────────
    eliminated_income:   Decimal   # dirty_income - clean_income
    eliminated_expense:  Decimal   # dirty_expense - clean_expense


@dataclass
class ConsolidatedPnL:
    """Консолидированный P&L по методу начислений."""
    company_ids:         list[UUID]
    start_date:          date
    end_date:            date
    # ── Грязный (с ВГО) ─────────────────────────────────────────────────────
    dirty_revenue:       Decimal
    dirty_expense:       Decimal
    dirty_profit:        Decimal
    # ── Консолидированный (без ВГО) ─────────────────────────────────────────
    clean_revenue:       Decimal
    clean_expense:       Decimal
    clean_profit:        Decimal
    # ── Исключённые ВГО ─────────────────────────────────────────────────────
    eliminated_revenue:  Decimal
    eliminated_expense:  Decimal


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _load_txn(
    db: AsyncSession,
    txn_id: UUID,
    label: str = "Транзакция",
) -> Transaction:
    """Загружает Transaction или бросает HTTP 404."""
    result = await db.execute(
        select(Transaction).where(
            and_(Transaction.id == txn_id, Transaction.is_deleted.is_(False))
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} {txn_id} не найдена или удалена.",
        )
    return txn


async def _load_doc(
    db: AsyncSession,
    doc_id: UUID,
    label: str = "Документ",
) -> AccrualDocument:
    """Загружает AccrualDocument или бросает HTTP 404."""
    result = await db.execute(
        select(AccrualDocument).where(
            and_(AccrualDocument.id == doc_id, AccrualDocument.is_deleted.is_(False))
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} {doc_id} не найден или удалён.",
        )
    return doc


async def _check_no_dup_txn_link(
    db: AsyncSession,
    source_id: UUID,
    dest_id: UUID,
) -> None:
    """
    Проверяет, что между двумя транзакциями ещё нет ВГО-связи
    (ни в прямом, ни в обратном направлении).
    """
    result = await db.execute(
        select(IntraGroupLink.id).where(
            or_(
                and_(
                    IntraGroupLink.source_transaction_id == source_id,
                    IntraGroupLink.destination_transaction_id == dest_id,
                ),
                and_(
                    IntraGroupLink.source_transaction_id == dest_id,
                    IntraGroupLink.destination_transaction_id == source_id,
                ),
            )
        ).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"ВГО-связь между транзакциями "
                f"{source_id} и {dest_id} уже существует."
            ),
        )


async def _check_no_dup_accrual_link(
    db: AsyncSession,
    source_id: UUID,
    dest_id: UUID,
) -> None:
    """
    Проверяет, что между двумя документами ещё нет ВГО-связи
    (ни в прямом, ни в обратном направлении).
    """
    result = await db.execute(
        select(IntraGroupLink.id).where(
            or_(
                and_(
                    IntraGroupLink.source_accrual_id == source_id,
                    IntraGroupLink.destination_accrual_id == dest_id,
                ),
                and_(
                    IntraGroupLink.source_accrual_id == dest_id,
                    IntraGroupLink.destination_accrual_id == source_id,
                ),
            )
        ).limit(1)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"ВГО-связь между документами "
                f"{source_id} и {dest_id} уже существует."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# СОЗДАНИЕ ВГО-СВЯЗЕЙ
# ─────────────────────────────────────────────────────────────────────────────


async def create_intra_group_transaction_link(
    db:           AsyncSession,
    source_txn_id: UUID,
    dest_txn_id:  UUID,
    amount:       Decimal,
) -> IntraGroupLinkResult:
    """
    Связывает два платежа внутри холдинга (денежный перевод между своими счётами).

    Типичный сценарий:
      ООО «Альфа» перевела ООО «Бета» 500 000 ₽ → у Альфы EXPENSE, у Беты INCOME.
      После link обе транзакции помечаются is_intra_group=True
      и исключаются из консолидированного ДДС.

    Проверки:
      1. amount > 0
      2. source ≠ destination
      3. Обе транзакции существуют и не удалены
      4. amount ≤ min(source.amount, dest.amount_base_currency)
      5. Между этой парой нет существующего линка

    Raises:
        HTTPException 404  — транзакция не найдена
        HTTPException 409  — дублирующийся линк
        HTTPException 422  — нарушение суммы или self-link
    """
    amount = _q(amount)
    if amount <= ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Сумма ВГО-связи должна быть больше нуля.",
        )
    if source_txn_id == dest_txn_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нельзя связать транзакцию саму с собой.",
        )

    src = await _load_txn(db, source_txn_id, "Источник")
    dst = await _load_txn(db, dest_txn_id,   "Получатель")

    # Сумма связи не должна превышать меньшую из двух транзакций
    max_linkable = _q(min(src.amount_base_currency, dst.amount_base_currency))
    if amount > max_linkable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма связи {amount} превышает допустимый максимум {max_linkable} "
                f"(min из {src.amount_base_currency} и {dst.amount_base_currency})."
            ),
        )

    await _check_no_dup_txn_link(db, source_txn_id, dest_txn_id)

    # Создаём связь и помечаем обе транзакции как ВГО
    link = IntraGroupLink(
        id=uuid.uuid4(),
        source_transaction_id=source_txn_id,
        destination_transaction_id=dest_txn_id,
        linked_amount=amount,
    )
    db.add(link)

    src.is_intra_group = True
    dst.is_intra_group = True

    await db.flush()

    return IntraGroupLinkResult(
        link_id=link.id,
        source_id=source_txn_id,
        destination_id=dest_txn_id,
        linked_amount=amount,
        link_type="transaction",
    )


async def create_intra_group_accrual_link(
    db:            AsyncSession,
    source_doc_id: UUID,
    dest_doc_id:   UUID,
    amount:        Decimal,
) -> IntraGroupLinkResult:
    """
    Связывает два акта внутренней купли-продажи между юрлицами холдинга.

    Типичный сценарий:
      ООО «Альфа» выставила ООО «Бета» Акт на 1 000 000 ₽ →
        у Альфы AccrualDocument(type=REVENUE), у Беты AccrualDocument(type=EXPENSE).
      После link оба документа помечаются is_intra_group=True
      и исключаются из консолидированного P&L.

    Проверки:
      1. amount > 0
      2. source ≠ destination
      3. Оба документа существуют и не удалены
      4. amount ≤ min(source.amount, dest.amount)
      5. Между этой парой нет существующего линка

    Raises:
        HTTPException 404  — документ не найден
        HTTPException 409  — дублирующийся линк
        HTTPException 422  — нарушение суммы или self-link
    """
    amount = _q(amount)
    if amount <= ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Сумма ВГО-связи должна быть больше нуля.",
        )
    if source_doc_id == dest_doc_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Нельзя связать документ сам с собой.",
        )

    src = await _load_doc(db, source_doc_id, "Источник (Реализация)")
    dst = await _load_doc(db, dest_doc_id,   "Получатель (Закупка)")

    max_linkable = _q(min(src.amount, dst.amount))
    if amount > max_linkable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма связи {amount} превышает допустимый максимум {max_linkable} "
                f"(min из {src.amount} и {dst.amount})."
            ),
        )

    await _check_no_dup_accrual_link(db, source_doc_id, dest_doc_id)

    link = IntraGroupLink(
        id=uuid.uuid4(),
        source_accrual_id=source_doc_id,
        destination_accrual_id=dest_doc_id,
        linked_amount=amount,
    )
    db.add(link)

    src.is_intra_group = True
    dst.is_intra_group = True

    await db.flush()

    return IntraGroupLinkResult(
        link_id=link.id,
        source_id=source_doc_id,
        destination_id=dest_doc_id,
        linked_amount=amount,
        link_type="accrual",
    )


# ─────────────────────────────────────────────────────────────────────────────
# КОНСОЛИДИРОВАННЫЙ ДДС
# ─────────────────────────────────────────────────────────────────────────────


async def get_consolidated_dds_totals(
    db:          AsyncSession,
    company_ids: list[UUID],
    start_date:  date,
    end_date:    date,
) -> ConsolidatedDDS:
    """
    Суммарные приходы и расходы по компаниям холдинга за период.

    Один SQL-запрос с условной агрегацией возвращает 4 числа:
      dirty_income, dirty_expense   — весь оборот (включая ВГО)
      clean_income, clean_expense   — только внешние операции (is_intra_group=False)

    Разница dirty - clean = исключённые ВГО.

    Аргументы:
        company_ids — список UUID компаний холдинга (1..N юрлиц)
        start_date  — начало периода (по payment_date транзакций)
        end_date    — конец периода
    """
    if not company_ids:
        return ConsolidatedDDS(
            company_ids=[], start_date=start_date, end_date=end_date,
            dirty_income=ZERO, dirty_expense=ZERO, dirty_net=ZERO,
            clean_income=ZERO, clean_expense=ZERO, clean_net=ZERO,
            eliminated_income=ZERO, eliminated_expense=ZERO,
        )

    is_income  = Transaction.transaction_type == TransactionType.INCOME
    is_expense = Transaction.transaction_type == TransactionType.EXPENSE
    is_clean   = Transaction.is_intra_group.is_(False)

    result = await db.execute(
        select(
            # ── Грязный оборот ───────────────────────────────────────────────
            func.coalesce(
                func.sum(case((is_income,  Transaction.amount_base_currency), else_=ZERO)),
                ZERO
            ).label("dirty_income"),
            func.coalesce(
                func.sum(case((is_expense, Transaction.amount_base_currency), else_=ZERO)),
                ZERO
            ).label("dirty_expense"),
            # ── Чистый оборот (без ВГО) ──────────────────────────────────────
            func.coalesce(
                func.sum(case((and_(is_income,  is_clean), Transaction.amount_base_currency), else_=ZERO)),
                ZERO
            ).label("clean_income"),
            func.coalesce(
                func.sum(case((and_(is_expense, is_clean), Transaction.amount_base_currency), else_=ZERO)),
                ZERO
            ).label("clean_expense"),
        )
        .where(
            and_(
                Transaction.company_id.in_(company_ids),
                Transaction.payment_date.between(start_date, end_date),
                Transaction.is_deleted.is_(False),
            )
        )
    )
    row = result.one()

    dirty_income  = _q(Decimal(str(row.dirty_income)))
    dirty_expense = _q(Decimal(str(row.dirty_expense)))
    clean_income  = _q(Decimal(str(row.clean_income)))
    clean_expense = _q(Decimal(str(row.clean_expense)))

    return ConsolidatedDDS(
        company_ids=company_ids,
        start_date=start_date,
        end_date=end_date,
        dirty_income=dirty_income,
        dirty_expense=dirty_expense,
        dirty_net=_q(dirty_income - dirty_expense),
        clean_income=clean_income,
        clean_expense=clean_expense,
        clean_net=_q(clean_income - clean_expense),
        eliminated_income=_q(dirty_income - clean_income),
        eliminated_expense=_q(dirty_expense - clean_expense),
    )


# ─────────────────────────────────────────────────────────────────────────────
# КОНСОЛИДИРОВАННЫЙ P&L
# ─────────────────────────────────────────────────────────────────────────────


async def get_consolidated_pnl_totals(
    db:          AsyncSession,
    company_ids: list[UUID],
    start_date:  date,
    end_date:    date,
) -> ConsolidatedPnL:
    """
    Очищенная выручка и расходы по методу начислений (Accrual Documents).

    Один SQL-запрос с условной агрегацией по doc_date документов.
    is_intra_group=True — внутрихолдинговые Акты, исключаемые при консолидации.

    Аргументы:
        company_ids — список UUID компаний холдинга
        start_date  — начало периода (по doc_date документов)
        end_date    — конец периода
    """
    if not company_ids:
        return ConsolidatedPnL(
            company_ids=[], start_date=start_date, end_date=end_date,
            dirty_revenue=ZERO, dirty_expense=ZERO, dirty_profit=ZERO,
            clean_revenue=ZERO, clean_expense=ZERO, clean_profit=ZERO,
            eliminated_revenue=ZERO, eliminated_expense=ZERO,
        )

    is_revenue = AccrualDocument.doc_type == AccrualDocumentType.REVENUE
    is_expense = AccrualDocument.doc_type == AccrualDocumentType.EXPENSE
    is_clean   = AccrualDocument.is_intra_group.is_(False)

    result = await db.execute(
        select(
            # ── Грязный P&L ─────────────────────────────────────────────────
            func.coalesce(
                func.sum(case((is_revenue, AccrualDocument.amount), else_=ZERO)),
                ZERO
            ).label("dirty_revenue"),
            func.coalesce(
                func.sum(case((is_expense, AccrualDocument.amount), else_=ZERO)),
                ZERO
            ).label("dirty_expense"),
            # ── Чистый P&L (без ВГО) ─────────────────────────────────────────
            func.coalesce(
                func.sum(case((and_(is_revenue, is_clean), AccrualDocument.amount), else_=ZERO)),
                ZERO
            ).label("clean_revenue"),
            func.coalesce(
                func.sum(case((and_(is_expense, is_clean), AccrualDocument.amount), else_=ZERO)),
                ZERO
            ).label("clean_expense"),
        )
        .where(
            and_(
                AccrualDocument.company_id.in_(company_ids),
                AccrualDocument.doc_date.between(start_date, end_date),
                AccrualDocument.is_deleted.is_(False),
            )
        )
    )
    row = result.one()

    dirty_revenue  = _q(Decimal(str(row.dirty_revenue)))
    dirty_expense  = _q(Decimal(str(row.dirty_expense)))
    clean_revenue  = _q(Decimal(str(row.clean_revenue)))
    clean_expense  = _q(Decimal(str(row.clean_expense)))

    return ConsolidatedPnL(
        company_ids=company_ids,
        start_date=start_date,
        end_date=end_date,
        dirty_revenue=dirty_revenue,
        dirty_expense=dirty_expense,
        dirty_profit=_q(dirty_revenue - dirty_expense),
        clean_revenue=clean_revenue,
        clean_expense=clean_expense,
        clean_profit=_q(clean_revenue - clean_expense),
        eliminated_revenue=_q(dirty_revenue - clean_revenue),
        eliminated_expense=_q(dirty_expense - clean_expense),
    )
