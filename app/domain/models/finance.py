"""
Доменный слой: финансовые модели.
Таблицы: companies, accounts, categories, transactions.
Все денежные поля — Numeric(15, 2), обязательный company_id для мультиарендности.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.domain.models.saas import Subscription, UserCompanyRole


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------


class TaxRegime(str, enum.Enum):
    """Налоговые режимы РФ."""
    OSN        = "osn"         # ОСНО — Общая система налогообложения
    USN_INCOME = "usn_income"  # УСН «Доходы» 6%
    USN_PROFIT = "usn_profit"  # УСН «Доходы минус расходы» 15%
    PATENT     = "patent"      # Патентная система (ПСН)
    ENVD       = "envd"        # ЕНВД (устаревший, но встречается)
    ESHN       = "eshn"        # ЕСХН — Единый сельхозналог
    NPD        = "npd"         # НПД — Налог на профессиональный доход (самозанятые)


class AccountType(str, enum.Enum):
    CHECKING = "checking"          # Расчётный счёт
    SAVINGS = "savings"            # Сберегательный счёт
    CASH = "cash"                  # Наличные
    CREDIT = "credit"              # Кредитный счёт
    INVESTMENT = "investment"      # Инвестиционный счёт


class TransactionType(str, enum.Enum):
    INCOME = "income"              # Поступление
    EXPENSE = "expense"            # Расход
    TRANSFER = "transfer"          # Перевод между счетами


class CategoryType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionStatus(str, enum.Enum):
    DRAFT = "draft"                # Черновик (не учитывается в отчётах)
    CONFIRMED = "confirmed"        # Подтверждена
    RECONCILED = "reconciled"      # Сверена с банком


class Currency(str, enum.Enum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    CNY = "CNY"
    KZT = "KZT"


# ---------------------------------------------------------------------------
# Базовый миксин с аудит-полями
# ---------------------------------------------------------------------------


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------


class Company(Base, TimestampMixin, SoftDeleteMixin):
    """
    Корневой агрегат мультиарендности.
    Каждый клиент (бизнес) — отдельная запись Company.
    Все финансовые объекты изолированы через company_id.
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    inn: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency_enum"), default=Currency.RUB, nullable=False
    )
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", nullable=False
    )
    tax_regime: Mapped[TaxRegime] = mapped_column(
        Enum(TaxRegime, name="tax_regime_enum"),
        default=TaxRegime.USN_INCOME,
        nullable=False,
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Связи
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    categories: Mapped[list["Category"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    roles: Mapped[list["UserCompanyRole"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_companies_inn", "inn"),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class Account(Base, TimestampMixin, SoftDeleteMixin):
    """
    Финансовый счёт компании (банковский, кассовый и т.д.).
    Баланс хранится в Numeric(15,2) — без округления с плавающей точкой.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type_enum"), nullable=False
    )
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency_enum"),
        nullable=False,
    )
    # Начальный остаток на момент заведения счёта
    initial_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False
    )
    # Кэшированный текущий баланс (денормализация для производительности)
    current_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), default=Decimal("0.00"), nullable=False
    )
    bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Идентификатор для автоматической банковской синхронизации
    bank_account_external_id: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # #RRGGBB
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Связи
    company: Mapped["Company"] = relationship(back_populates="accounts")
    transactions_as_source: Mapped[list["Transaction"]] = relationship(
        foreign_keys="Transaction.account_id", back_populates="account"
    )
    transactions_as_destination: Mapped[list["Transaction"]] = relationship(
        foreign_keys="Transaction.destination_account_id",
        back_populates="destination_account",
    )
    # Подключение банковского API для этого счёта (1:1, Sprint 8)
    bank_connection: Mapped[Optional["BankConnection"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="BankConnection.account_id",
        back_populates="account",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_accounts_company_id", "company_id"),
        Index("ix_accounts_company_type", "company_id", "account_type"),
        UniqueConstraint(
            "company_id", "bank_account_external_id",
            name="uq_accounts_company_external_id"
        ),
    )

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r} company_id={self.company_id}>"


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


class Category(Base, TimestampMixin, SoftDeleteMixin):
    """
    Иерархическая статья движения денежных средств (ДДС) или P&L.
    Поддерживает два уровня: родительская категория → дочерняя.
    """

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category_type: Mapped[CategoryType] = mapped_column(
        Enum(CategoryType, name="category_type_enum"), nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Системные (встроенные) категории нельзя удалить или переименовать
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Связи
    company: Mapped["Company"] = relationship(back_populates="categories")
    parent: Mapped[Optional["Category"]] = relationship(
        remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(back_populates="parent")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="category"
    )

    __table_args__ = (
        Index("ix_categories_company_id", "company_id"),
        Index("ix_categories_company_type", "company_id", "category_type"),
        Index("ix_categories_parent_id", "parent_id"),
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r} type={self.category_type}>"


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class Transaction(Base, TimestampMixin, SoftDeleteMixin):
    """
    Финансовая операция.

    Двойная дата для разделения ДДС и P&L:
    - payment_date  → дата фактического движения денег (Cash Flow / ДДС)
    - accrual_date  → дата начисления (P&L / управленческий учёт)

    Перевод между счетами хранится как одна запись с типом TRANSFER
    и заполненным destination_account_id.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Только для TRANSFER
    destination_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Пользователь, создавший операцию
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Связь с контрагентом из справочника (опционально)
    counterparty_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("counterparties.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Связь со счётом/актом (для закрытия дебиторки/кредиторки)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    # ID парной транзакции перевода (DEBIT ↔ CREDIT одного трансфера между счетами)
    transfer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Привязка к проекту (опционально, для проектного учёта)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Привязка к карточке имущества (транзакция амортизации или продажи)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Привязка к кредиту (транзакция погашения тела или процентов)
    loan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("loans.id", ondelete="SET NULL"),
        nullable=True,
    )

    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type_enum"), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status_enum"),
        default=TransactionStatus.CONFIRMED,
        nullable=False,
    )

    # Финансовые поля — строго Numeric(15,2)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency_enum"), nullable=False
    )
    # Обменный курс к базовой валюте компании (1.0 если валюты совпадают)
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), default=Decimal("1.000000"), nullable=False
    )
    # Сумма в базовой валюте компании
    amount_base_currency: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False
    )

    # Двойная дата: ДДС vs P&L
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    accrual_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counterparty: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Внешний ID из банка для идемпотентности при синхронизации
    bank_transaction_id: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    # Произвольные теги и метаданные для AI-классификации
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Флаг: операция была классифицирована AI-парсером
    ai_classified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    ai_confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4), nullable=True  # 0.0000 – 1.0000
    )

    # ── Холдинговые связи (Sprint 11) ────────────────────────────────────────
    # True = внутригрупповая операция (ВГО): перевод между юрлицами холдинга.
    # При консолидации такие операции исключаются из отчётности.
    is_intra_group: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        doc="ВГО-флаг: True = внутрихолдинговый перевод, исключается при консолидации",
    )

    # ── НДС (Sprint 10) ──────────────────────────────────────────────────────
    # NULL = без НДС (УСН, освобождение и т.д.)
    # 0.00 = НДС 0% (экспорт)  /  10.00 = 10%  /  20.00 = 20%
    vat_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True,
        doc="Ставка НДС %, NULL = без НДС",
    )
    # Конкретная сумма налога, выделенная из amount
    vat_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True,
        doc="Сумма НДС в рублях, выделенная из суммы операции",
    )

    # Связи
    company: Mapped["Company"] = relationship(back_populates="transactions")
    account: Mapped["Account"] = relationship(
        foreign_keys=[account_id], back_populates="transactions_as_source"
    )
    destination_account: Mapped[Optional["Account"]] = relationship(
        foreign_keys=[destination_account_id],
        back_populates="transactions_as_destination",
    )
    category: Mapped[Optional["Category"]] = relationship(
        back_populates="transactions"
    )
    # Ленивые связи к модулю counterparties (импорт отложен через TYPE_CHECKING)
    invoice: Mapped[Optional["ContractOrInvoice"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="Transaction.invoice_id",
        back_populates="transactions",
    )
    # Привязка к проекту (импорт из projects.py, отложен через TYPE_CHECKING)
    project: Mapped[Optional["Project"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="Transaction.project_id",
        back_populates="transactions",
    )
    # Привязка к основному средству (импорт из assets_loans.py)
    asset: Mapped[Optional["Asset"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="Transaction.asset_id",
        back_populates="transactions",
    )
    # Привязка к кредиту (импорт из assets_loans.py)
    loan: Mapped[Optional["Loan"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="Transaction.loan_id",
        back_populates="transactions",
    )
    # Связи с документами начислений M2M (Sprint 9)
    accrual_links: Mapped[list["TransactionAccrualLink"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="TransactionAccrualLink.transaction_id",
        back_populates="transaction",
        cascade="all, delete-orphan",
    )
    # ВГО-связи (Sprint 11): эта транзакция — источник межфирменного перевода
    intra_group_as_source: Mapped[list["IntraGroupLink"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="IntraGroupLink.source_transaction_id",
        back_populates="source_transaction",
    )
    # ВГО-связи (Sprint 11): эта транзакция — получатель межфирменного перевода
    intra_group_as_dest: Mapped[list["IntraGroupLink"]] = relationship(  # type: ignore[name-defined]
        foreign_keys="IntraGroupLink.destination_transaction_id",
        back_populates="destination_transaction",
    )

    __table_args__ = (
        Index("ix_transactions_company_payment_date", "company_id", "payment_date"),
        Index("ix_transactions_company_accrual_date", "company_id", "accrual_date"),
        Index("ix_transactions_account_id",     "account_id"),
        Index("ix_transactions_category_id",    "category_id"),
        Index("ix_transactions_company_type",   "company_id", "transaction_type"),
        Index("ix_transactions_company_status", "company_id", "status"),
        Index("ix_transactions_counterparty_id","counterparty_id"),
        Index("ix_transactions_invoice_id",     "invoice_id"),
        Index("ix_transactions_transfer_id",    "transfer_id"),   # создан в sprint5_core_expansion
        Index("ix_transactions_project_id",     "project_id"),
        Index("ix_transactions_asset_id",       "asset_id"),
        Index("ix_transactions_loan_id",        "loan_id"),
        UniqueConstraint(
            "account_id", "bank_transaction_id",
            name="uq_transactions_account_bank_id"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} type={self.transaction_type} "
            f"amount={self.amount} {self.currency} date={self.payment_date}>"
        )
