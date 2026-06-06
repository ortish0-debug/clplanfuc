"""
Доменный слой: Холдинговые связи и консолидация (Спринт 11).

Внутригрупповые операции (ВГО) — движение денег и товаров между своими юрлицами.
При консолидации холдинга такие операции должны быть исключены из отчётности
для избежания двойного учёта (если одна операция описана и у продавца, и у покупателя).

Архитектура:
  IntraGroupLink объединяет либо:
    - две Transaction (внутренний перевод денег между счётами своих фирм)
    - два AccrualDocument (внутренняя купля-продажа товаров/услуг)

  Не допускается смешивать: нельзя связать Transaction с AccrualDocument.
  Ограничения контролируются CheckConstraint на уровне БД.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    func,
    and_,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.domain.models.finance import Transaction
    from app.domain.models.accruals import AccrualDocument


class IntraGroupLink(Base):
    """
    Связь между двумя операциями внутри холдинга (ВГО).

    Допустимые комбинации:
      1. source_transaction_id + destination_transaction_id (не null)
         источник = исходящий платёж, получатель = входящий платёж

      2. source_accrual_id + destination_accrual_id (не null)
         источник = реализация, получатель = закупка

    Недопустимые комбинации:
      - Транзакция с документом (разные сущности)
      - Оба источника null или оба получателя null
      - Обе стороны одного типа (txn1 + txn2) и одна из них null

    Инварианты (контролируются CheckConstraint):
      XOR((source_transaction_id IS NOT NULL AND destination_transaction_id IS NOT NULL),
          (source_accrual_id IS NOT NULL AND destination_accrual_id IS NOT NULL))
      → Одна из пар транзакций или одна из пар актов, но не смешивание.
    """

    __tablename__ = "intra_group_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Ветка 1: Транзакции (переводы денег между счётами) ────────────────────
    source_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=True,
        doc="Исходящий платёж (из счёта юрлица 1)",
    )
    destination_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=True,
        doc="Входящий платёж (на счёт юрлица 2)",
    )

    # ── Ветка 2: Документы начисления (купля-продажа товаров/услуг) ──────────
    source_accrual_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accrual_documents.id", ondelete="CASCADE"),
        nullable=True,
        doc="Реализация товаров/услуг юрлицом 1 (у юрлица 1 это выручка)",
    )
    destination_accrual_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accrual_documents.id", ondelete="CASCADE"),
        nullable=True,
        doc="Закупка / приёмка у юрлица 1 (у юрлица 2 это расход)",
    )

    # ── Связанная сумма ──────────────────────────────────────────────────────
    linked_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Сумма внутригруппового перемещения, ₽. Может быть меньше одной из сторон (частичное связывание)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Связи ────────────────────────────────────────────────────────────────
    source_transaction: Mapped[Optional["Transaction"]] = relationship(  # type: ignore[name-defined]
        foreign_keys=[source_transaction_id],
        back_populates="intra_group_as_source",
    )
    destination_transaction: Mapped[Optional["Transaction"]] = relationship(  # type: ignore[name-defined]
        foreign_keys=[destination_transaction_id],
        back_populates="intra_group_as_dest",
    )
    source_accrual: Mapped[Optional["AccrualDocument"]] = relationship(  # type: ignore[name-defined]
        foreign_keys=[source_accrual_id],
        back_populates="intra_group_as_source",
    )
    destination_accrual: Mapped[Optional["AccrualDocument"]] = relationship(  # type: ignore[name-defined]
        foreign_keys=[destination_accrual_id],
        back_populates="intra_group_as_dest",
    )

    __table_args__ = (
        # Инвариант: либо обе стороны транзакции, либо обе стороны актов.
        # NOT BOTH AND NOT NEITHER.
        CheckConstraint(
            """
            (
              (source_transaction_id IS NOT NULL AND destination_transaction_id IS NOT NULL
               AND source_accrual_id IS NULL AND destination_accrual_id IS NULL)
              OR
              (source_accrual_id IS NOT NULL AND destination_accrual_id IS NOT NULL
               AND source_transaction_id IS NULL AND destination_transaction_id IS NULL)
            )
            """,
            name="ck_intra_group_either_txn_or_accrual",
        ),
        Index("ix_intra_group_source_txn", "source_transaction_id"),
        Index("ix_intra_group_dest_txn", "destination_transaction_id"),
        Index("ix_intra_group_source_accrual", "source_accrual_id"),
        Index("ix_intra_group_dest_accrual", "destination_accrual_id"),
    )

    def __repr__(self) -> str:
        if self.source_transaction_id:
            return (
                f"<IntraGroupLink txn_link "
                f"src={self.source_transaction_id} "
                f"→ dst={self.destination_transaction_id} "
                f"amt={self.linked_amount}>"
            )
        else:
            return (
                f"<IntraGroupLink accrual_link "
                f"src={self.source_accrual_id} "
                f"→ dst={self.destination_accrual_id} "
                f"amt={self.linked_amount}>"
            )
