"""
Управленческий План счетов и Двойная запись (Спринт 14).

Архитектура:
  AccountChart — карточка счета (код, название, категория актив/обязательство/капитал/доходы/расходы)
  JournalEntry — запись в журнал проводок (дата, описание, тип документа-триггера)
  LedgerLine — строка проводки: дебет или кредит на счет (double-entry принцип)

Принцип двойной записи (double-entry):
  Каждая операция создаёт JournalEntry с 2+ строками LedgerLine:
    - Дебет одного счета
    - Кредит другого счета
  Сумма дебетов = Сумма кредитов (баланс)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import TimestampMixin

if TYPE_CHECKING:
    from app.domain.models.finance import Company


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕЧИСЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


class AccountCategory(str, enum.Enum):
    """Категория счета по плану счетов."""
    ASSET = "asset"           # Активы
    LIABILITY = "liability"   # Обязательства
    EQUITY = "equity"         # Капитал/Собственный капитал
    REVENUE = "revenue"       # Доходы
    EXPENSE = "expense"       # Расходы


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT CHART — Карточка счета
# ─────────────────────────────────────────────────────────────────────────────


class AccountChart(Base, TimestampMixin):
    """
    Счет в плане счетов компании.

    Примеры:
      - '1100' (ASSET): Касса
      - '1200' (ASSET): Расчетный счет
      - '2100' (LIABILITY): Расчеты с поставщиками
      - '3100' (EQUITY): Уставный капитал
      - '4100' (REVENUE): Выручка от продажи
      - '5100' (EXPENSE): Себестоимость реализованной продукции
    """

    __tablename__ = "accounts_chart"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="Код счета (напр., '1100', '2100', '5100')",
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        doc="Наименование счета (напр., 'Касса', 'Расчетный счет')",
    )
    category: Mapped[AccountCategory] = mapped_column(
        Enum(
            AccountCategory,
            name="account_category_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        doc="Категория счета: asset, liability, equity, revenue, expense",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        doc="Счет активен (закрытые счета = False)",
    )

    # Связи
    company: Mapped["Company"] = relationship()
    ledger_lines: Mapped[list["LedgerLine"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_accounts_chart_company_id", "company_id"),
        Index("ix_accounts_chart_company_code", "company_id", "code"),
    )

    def __repr__(self) -> str:
        return f"<AccountChart code={self.code!r} name={self.name!r} category={self.category}>"


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL ENTRY — Запись в журнал проводок
# ─────────────────────────────────────────────────────────────────────────────


class JournalEntry(Base, TimestampMixin):
    """
    Запись в журнал проводок (дневник бухгалтера).

    Одна запись = одна операция с 2+ строками LedgerLine (дебет-кредит).
    Сумма всех дебетов в entry = сумме всех кредитов.

    Примеры:
      - Приход денег на расчетный счет:
        * LedgerLine(account='1200', debit=1000)
        * LedgerLine(account='4100', credit=1000)
      - Покупка товара:
        * LedgerLine(account='41', debit=500)   [материалы]
        * LedgerLine(account='60', credit=500)  [счета к оплате]
    """

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_date: Mapped[date] = mapped_column(
        Date, nullable=False,
        doc="Дата операции (дата проводки)",
    )
    description: Mapped[str] = mapped_column(
        String(512), nullable=False,
        doc="Описание операции (напр., 'Приход денег на РС', 'Покупка товара')",
    )
    source_doc_type: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
        doc="Тип документа-триггера: invoice, stock_operation, accrual, transaction и т.д.",
    )
    source_doc_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        doc="UUID документа-триггера (напр., StockOperation.id)",
    )

    # Связи
    company: Mapped["Company"] = relationship()
    lines: Mapped[list["LedgerLine"]] = relationship(
        back_populates="journal_entry",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_journal_entries_company_id", "company_id"),
        Index("ix_journal_entries_operation_date", "company_id", "operation_date"),
    )

    def __repr__(self) -> str:
        return f"<JournalEntry id={self.id} date={self.operation_date} desc={self.description[:30]!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# LEDGER LINE — Строка проводки (дебет или кредит)
# ─────────────────────────────────────────────────────────────────────────────


class LedgerLine(Base):
    """
    Одна строка проводки в журнале.

    Содержит:
      - account_chart_id: на какой счет проводится
      - debit: сумма по дебету (если > 0)
      - credit: сумма по кредиту (если > 0)

    Constraint: XOR(debit > 0, credit > 0) — либо дебет, либо кредит, но не оба.
    """

    __tablename__ = "ledger_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_chart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_chart.id", ondelete="RESTRICT"),
        nullable=False,
    )
    debit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False,
        doc="Сумма по дебету (сч. активов/расходов увеличивается)",
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False,
        doc="Сумма по кредиту (сч. пассивов/доходов увеличивается)",
    )

    # Связи
    journal_entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["AccountChart"] = relationship(back_populates="ledger_lines")

    __table_args__ = (
        Index("ix_ledger_lines_account_id", "account_chart_id"),
        Index("ix_ledger_lines_journal_id", "journal_entry_id"),
        # XOR: либо debit > 0, либо credit > 0 (не оба, не ни один)
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="check_debit_credit_xor",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<LedgerLine id={self.id} "
            f"account={self.account_chart_id} "
            f"debit={self.debit} credit={self.credit}>"
        )
