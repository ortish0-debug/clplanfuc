"""
Сервис: Взаиморасчёты и закрытие обязательств (Спринт 9).

Реализует метод начислений (accrual basis):
  - Привязка платежей к документам начислений (Актам, Накладным)
  - Автоматический пересчёт статуса оплаты документа
  - Калькулятор дебиторской/кредиторской задолженности по контрагентам

Бизнес-инварианты, которые гарантирует сервис:
  1. linked_amount по одной записи ≤ остаток по документу
  2. linked_amount по одной записи ≤ свободный остаток по транзакции
  3. Один платёж не дублируется на один и тот же документ
  4. payment_status документа всегда синхронен с суммой его привязок
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.accruals import (
    AccrualDocument,
    AccrualDocumentType,
    AccrualStatus,
    TransactionAccrualLink,
)
from app.domain.models.counterparties import Counterparty
from app.domain.models.finance import Transaction

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗУЛЬТАТЫ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LinkResult:
    """Итог привязки платежа к документу."""
    link_id:          UUID
    transaction_id:   UUID
    accrual_id:       UUID
    linked_amount:    Decimal
    new_doc_status:   AccrualStatus
    doc_remaining:    Decimal    # остаток суммы документа после привязки
    txn_remaining:    Decimal    # свободный остаток транзакции после привязки


@dataclass
class UnlinkResult:
    """Итог снятия привязки платежа с документа."""
    accrual_id:     UUID
    new_doc_status: AccrualStatus
    doc_remaining:  Decimal      # освобождённый остаток документа


@dataclass
class CounterpartyBalance:
    """Текущее сальдо взаиморасчётов с одним контрагентом."""
    counterparty_id:        UUID
    counterparty_name:      str
    counterparty_inn:       Optional[str]

    # Дебиторская задолженность (REVENUE — нам должны)
    receivable_total:       Decimal   # сумма всех выставленных актов
    receivable_linked:      Decimal   # оплачено по ним
    receivable_outstanding: Decimal   # остаток дебиторки

    # Кредиторская задолженность (EXPENSE — мы должны)
    payable_total:          Decimal   # сумма всех полученных актов
    payable_linked:         Decimal   # оплачено по ним
    payable_outstanding:    Decimal   # остаток кредиторки

    # Чистая позиция (> 0 → нам должны больше, чем мы)
    net_position:           Decimal


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _load_doc_or_404(
    db: AsyncSession,
    accrual_id: UUID,
    company_id: Optional[UUID] = None,
) -> AccrualDocument:
    """Загружает AccrualDocument, проверяя принадлежность компании."""
    filters = [
        AccrualDocument.id == accrual_id,
        AccrualDocument.is_deleted.is_(False),
    ]
    if company_id:
        filters.append(AccrualDocument.company_id == company_id)

    result = await db.execute(select(AccrualDocument).where(and_(*filters)))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Документ начисления {accrual_id} не найден.",
        )
    return doc


async def _load_txn_or_404(
    db: AsyncSession,
    transaction_id: UUID,
    company_id: Optional[UUID] = None,
) -> Transaction:
    """Загружает Transaction, проверяя принадлежность компании."""
    filters = [
        Transaction.id == transaction_id,
        Transaction.is_deleted.is_(False),
    ]
    if company_id:
        filters.append(Transaction.company_id == company_id)

    result = await db.execute(select(Transaction).where(and_(*filters)))
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Транзакция {transaction_id} не найдена.",
        )
    return txn


async def _sum_linked_to_doc(
    db: AsyncSession,
    accrual_document_id: UUID,
    exclude_link_id: Optional[UUID] = None,
) -> Decimal:
    """Суммирует linked_amount всех привязок к документу."""
    filters = [TransactionAccrualLink.accrual_document_id == accrual_document_id]
    if exclude_link_id:
        filters.append(TransactionAccrualLink.id != exclude_link_id)

    result = await db.execute(
        select(func.coalesce(func.sum(TransactionAccrualLink.linked_amount), 0))
        .where(and_(*filters))
    )
    return _q(Decimal(str(result.scalar() or "0")))


async def _sum_linked_from_txn(
    db: AsyncSession,
    transaction_id: UUID,
    exclude_link_id: Optional[UUID] = None,
) -> Decimal:
    """Суммирует linked_amount всех привязок из транзакции к любым документам."""
    filters = [TransactionAccrualLink.transaction_id == transaction_id]
    if exclude_link_id:
        filters.append(TransactionAccrualLink.id != exclude_link_id)

    result = await db.execute(
        select(func.coalesce(func.sum(TransactionAccrualLink.linked_amount), 0))
        .where(and_(*filters))
    )
    return _q(Decimal(str(result.scalar() or "0")))


async def _recalculate_doc_status(
    db: AsyncSession,
    doc: AccrualDocument,
) -> AccrualStatus:
    """
    Пересчитывает и сохраняет payment_status документа.

    Вызывается ПОСЛЕ db.flush() — чтобы новые/удалённые links были учтены.

    Логика:
      total_linked == 0                → UNPAID
      0 < total_linked < doc.amount    → PARTIALLY_PAID
      total_linked == doc.amount       → PAID
      total_linked > doc.amount        → OVERPAID (аномалия)
    """
    total_linked = await _sum_linked_to_doc(db, doc.id)

    if total_linked == ZERO:
        new_status = AccrualStatus.UNPAID
    elif total_linked < doc.amount:
        new_status = AccrualStatus.PARTIALLY_PAID
    elif total_linked == doc.amount:
        new_status = AccrualStatus.PAID
    else:
        new_status = AccrualStatus.OVERPAID

    doc.payment_status = new_status
    return new_status


# ─────────────────────────────────────────────────────────────────────────────
# LINK — Привязка платежа к документу
# ─────────────────────────────────────────────────────────────────────────────


async def link_transaction_to_accrual(
    db:                  AsyncSession,
    transaction_id:      UUID,
    accrual_document_id: UUID,
    amount:              Decimal,
    user_id:             Optional[UUID] = None,
) -> LinkResult:
    """
    Привязывает сумму из транзакции к документу начисления.

    Валидации (все бросают HTTPException):
      - amount > 0
      - Транзакция и документ принадлежат одной компании
      - Нет дублирующей привязки (transaction × accrual)
      - amount ≤ остаток по документу (не переплачиваем)
      - amount ≤ свободный остаток транзакции (не используем больше, чем есть)

    После создания связи обновляет AccrualDocument.payment_status.

    Возвращает LinkResult с итогами операции.
    """
    amount = _q(amount)
    if amount <= ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Сумма привязки должна быть больше нуля.",
        )

    # ── Загружаем сущности ────────────────────────────────────────────────
    doc = await _load_doc_or_404(db, accrual_document_id)
    txn = await _load_txn_or_404(db, transaction_id, company_id=doc.company_id)

    # ── Проверка: один платёж → один документ не дублируется ──────────────
    existing = await db.execute(
        select(TransactionAccrualLink.id).where(
            and_(
                TransactionAccrualLink.transaction_id      == transaction_id,
                TransactionAccrualLink.accrual_document_id == accrual_document_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Транзакция {transaction_id} уже привязана к документу "
                f"{accrual_document_id}. Удалите существующую связь перед повторной."
            ),
        )

    # ── Проверка остатка по документу ─────────────────────────────────────
    already_linked_to_doc = await _sum_linked_to_doc(db, accrual_document_id)
    doc_remaining = _q(doc.amount - already_linked_to_doc)

    if amount > doc_remaining:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма привязки {amount} превышает остаток по документу "
                f"{doc_remaining} (документ на {doc.amount}, уже привязано {already_linked_to_doc})."
            ),
        )

    # ── Проверка свободного остатка транзакции ────────────────────────────
    already_linked_from_txn = await _sum_linked_from_txn(db, transaction_id)
    txn_remaining = _q(txn.amount_base_currency - already_linked_from_txn)

    if amount > txn_remaining:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Сумма привязки {amount} превышает свободный остаток транзакции "
                f"{txn_remaining} (транзакция на {txn.amount_base_currency}, "
                f"уже распределено {already_linked_from_txn})."
            ),
        )

    # ── Создаём связь ─────────────────────────────────────────────────────
    link = TransactionAccrualLink(
        id=uuid.uuid4(),
        transaction_id=transaction_id,
        accrual_document_id=accrual_document_id,
        linked_amount=amount,
        linked_by_user_id=user_id,
    )
    db.add(link)
    await db.flush()   # чтобы _recalculate_doc_status учёл новую строку

    # ── Пересчитываем статус документа ───────────────────────────────────
    new_status = await _recalculate_doc_status(db, doc)
    await db.flush()

    return LinkResult(
        link_id=link.id,
        transaction_id=transaction_id,
        accrual_id=accrual_document_id,
        linked_amount=amount,
        new_doc_status=new_status,
        doc_remaining=_q(doc_remaining - amount),
        txn_remaining=_q(txn_remaining - amount),
    )


# ─────────────────────────────────────────────────────────────────────────────
# UNLINK — Снятие привязки
# ─────────────────────────────────────────────────────────────────────────────


async def unlink_transaction_from_accrual(
    db:      AsyncSession,
    link_id: UUID,
) -> UnlinkResult:
    """
    Удаляет связь «платёж ↔ документ» и пересчитывает статус документа.

    После удаления payment_status документа возвращается к актуальному
    на основе оставшихся привязок (UNPAID / PARTIALLY_PAID / OVERPAID).

    Raises:
        HTTPException 404 — если связь не найдена.
    """
    link_result = await db.execute(
        select(TransactionAccrualLink).where(TransactionAccrualLink.id == link_id)
    )
    link: Optional[TransactionAccrualLink] = link_result.scalar_one_or_none()
    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Связь {link_id} не найдена.",
        )

    accrual_id = link.accrual_document_id

    # Загружаем документ ДО удаления связи для обновления статуса
    doc_result = await db.execute(
        select(AccrualDocument).where(AccrualDocument.id == accrual_id)
    )
    doc: AccrualDocument = doc_result.scalar_one()

    # Удаляем связь
    await db.delete(link)
    await db.flush()   # чтобы SUM-запрос не считал удалённую строку

    # Пересчитываем статус
    new_status = await _recalculate_doc_status(db, doc)
    await db.flush()

    # Остаток документа = то, что теперь можно ещё привязать
    total_linked = await _sum_linked_to_doc(db, accrual_id)
    doc_remaining = _q(doc.amount - total_linked)

    return UnlinkResult(
        accrual_id=accrual_id,
        new_doc_status=new_status,
        doc_remaining=doc_remaining,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTY BALANCES — Калькулятор дебиторки/кредиторки
# ─────────────────────────────────────────────────────────────────────────────


async def get_counterparty_balances(
    db:               AsyncSession,
    company_id:       UUID,
    include_settled:  bool = False,
) -> list[CounterpartyBalance]:
    """
    Рассчитывает текущие сальдо взаиморасчётов с каждым контрагентом.

    Дебиторка (receivable) = REVENUE-документы — нам должны.
    Кредиторка (payable)   = EXPENSE-документы — мы должны.

    Аргументы:
        include_settled — включать ли контрагентов с нулевым сальдо
                          (все документы которых полностью закрыты).

    Логика:
        Для каждого контрагента суммируем amount из AccrualDocument
        и вычитаем SUM(linked_amount) из TransactionAccrualLink.
        outstanding = amount - linked ≥ 0 при отсутствии переплат.

    Возвращает список, отсортированный по |net_position| DESC.
    """

    # Подзапрос: суммарно привязано к каждому документу
    linked_subq = (
        select(
            TransactionAccrualLink.accrual_document_id.label("doc_id"),
            func.sum(TransactionAccrualLink.linked_amount).label("total_linked"),
        )
        .group_by(TransactionAccrualLink.accrual_document_id)
        .subquery("linked_totals")
    )

    stmt = (
        select(
            AccrualDocument.counterparty_id,
            Counterparty.name.label("counterparty_name"),
            Counterparty.inn.label("counterparty_inn"),
            AccrualDocument.doc_type,
            func.sum(AccrualDocument.amount).label("total_amount"),
            func.coalesce(
                func.sum(linked_subq.c.total_linked), 0
            ).label("total_linked"),
        )
        .join(Counterparty, AccrualDocument.counterparty_id == Counterparty.id)
        .outerjoin(
            linked_subq, AccrualDocument.id == linked_subq.c.doc_id
        )
        .where(
            and_(
                AccrualDocument.company_id == company_id,
                AccrualDocument.is_deleted.is_(False),
                Counterparty.is_deleted.is_(False),
            )
        )
        .group_by(
            AccrualDocument.counterparty_id,
            Counterparty.name,
            Counterparty.inn,
            AccrualDocument.doc_type,
        )
        .order_by(Counterparty.name, AccrualDocument.doc_type)
    )

    # Фильтруем нулевые сальдо если не нужно
    if not include_settled:
        stmt = stmt.having(
            func.sum(AccrualDocument.amount)
            > func.coalesce(func.sum(linked_subq.c.total_linked), 0)
        )

    rows = (await db.execute(stmt)).all()

    # ── Группируем строки по контрагенту ─────────────────────────────────
    accum: dict[UUID, dict] = {}
    for row in rows:
        cid = row.counterparty_id
        if cid not in accum:
            accum[cid] = {
                "counterparty_id":   cid,
                "counterparty_name": row.counterparty_name,
                "counterparty_inn":  row.counterparty_inn,
                "receivable_total":  ZERO,
                "receivable_linked": ZERO,
                "payable_total":     ZERO,
                "payable_linked":    ZERO,
            }

        total  = _q(Decimal(str(row.total_amount)))
        linked = _q(Decimal(str(row.total_linked)))

        if row.doc_type == AccrualDocumentType.REVENUE:
            accum[cid]["receivable_total"]  += total
            accum[cid]["receivable_linked"] += linked
        else:
            accum[cid]["payable_total"]  += total
            accum[cid]["payable_linked"] += linked

    # ── Строим итоговые объекты ───────────────────────────────────────────
    result: list[CounterpartyBalance] = []
    for data in accum.values():
        rec_out = _q(data["receivable_total"] - data["receivable_linked"])
        pay_out = _q(data["payable_total"]    - data["payable_linked"])
        net     = _q(rec_out - pay_out)

        result.append(CounterpartyBalance(
            counterparty_id=data["counterparty_id"],
            counterparty_name=data["counterparty_name"],
            counterparty_inn=data["counterparty_inn"],
            receivable_total=_q(data["receivable_total"]),
            receivable_linked=_q(data["receivable_linked"]),
            receivable_outstanding=rec_out,
            payable_total=_q(data["payable_total"]),
            payable_linked=_q(data["payable_linked"]),
            payable_outstanding=pay_out,
            net_position=net,
        ))

    # Сортировка: сначала самые крупные задолженности
    return sorted(result, key=lambda b: (-abs(b.net_position), b.counterparty_name))


# ─────────────────────────────────────────────────────────────────────────────
# СВОДКА ПО ОДНОМУ ДОКУМЕНТУ (для карточки)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DocumentClosure:
    """Детализация закрытия одного документа начисления."""
    accrual_id:     UUID
    doc_number:     str
    doc_date:       object          # date
    doc_type:       AccrualDocumentType
    total_amount:   Decimal
    total_linked:   Decimal         # суммарно оплачено
    remaining:      Decimal         # остаток к оплате
    payment_status: AccrualStatus
    links:          list[dict]      # [{link_id, transaction_id, amount, date}]


async def get_document_closure(
    db:         AsyncSession,
    accrual_id: UUID,
) -> DocumentClosure:
    """
    Возвращает детальное состояние закрытия документа:
    сумму, оплаченную часть и список платежей с суммами распределения.
    """
    doc = await _load_doc_or_404(db, accrual_id)

    links_result = await db.execute(
        select(
            TransactionAccrualLink.id,
            TransactionAccrualLink.transaction_id,
            TransactionAccrualLink.linked_amount,
            Transaction.payment_date,
            Transaction.description,
        )
        .join(Transaction, TransactionAccrualLink.transaction_id == Transaction.id)
        .where(TransactionAccrualLink.accrual_document_id == accrual_id)
        .order_by(Transaction.payment_date)
    )
    link_rows = links_result.all()

    total_linked = _q(sum((r.linked_amount for r in link_rows), ZERO))
    remaining    = _q(doc.amount - total_linked)

    links = [
        {
            "link_id":        str(r.id),
            "transaction_id": str(r.transaction_id),
            "linked_amount":  _q(r.linked_amount),
            "payment_date":   r.payment_date.isoformat(),
            "description":    r.description,
        }
        for r in link_rows
    ]

    return DocumentClosure(
        accrual_id=doc.id,
        doc_number=doc.doc_number,
        doc_date=doc.doc_date,
        doc_type=doc.doc_type,
        total_amount=doc.amount,
        total_linked=total_linked,
        remaining=remaining,
        payment_status=doc.payment_status,
        links=links,
    )
