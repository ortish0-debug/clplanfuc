"""
Pydantic v2 схемы для Управленческого Баланса и учёта задолженностей.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v.quantize(Decimal("0.01"))), return_type=str, when_used="json"),
]


# ─────────────────────────────────────────────────────────────────────────────
# BALANCE SHEET
# ─────────────────────────────────────────────────────────────────────────────


class BalanceItemSchema(BaseModel):
    """Строка отчёта баланса (счёт или подитог)."""
    name: str = Field(..., description="Название позиции")
    code: Optional[str] = Field(None, description="Код счёта")
    amount: DecimalStr = Field(..., description="Сумма по счёту")


class BalanceSectionSchema(BaseModel):
    """Подраздел баланса (например, 'Денежные средства', 'Текущие обязательства')."""
    title: str = Field(..., description="Название раздела")
    items: list[BalanceItemSchema] = Field(default_factory=list, description="Строки раздела")
    total: DecimalStr = Field(..., description="Итого по разделу")


class BalanceSheetResponse(BaseModel):
    """Полная форма баланса (активы = пассивы + капитал)."""
    company_id: UUID = Field(..., description="UUID компании")
    target_date: date = Field(..., description="Дата на которую составлен баланс")

    assets: list[BalanceSectionSchema] = Field(
        default_factory=list,
        description="Разделы активов"
    )
    total_assets: DecimalStr = Field(..., description="Итого активы")

    liabilities: list[BalanceSectionSchema] = Field(
        default_factory=list,
        description="Разделы пассивов"
    )
    equity: list[BalanceSectionSchema] = Field(
        default_factory=list,
        description="Разделы капитала"
    )
    total_liabilities_equity: DecimalStr = Field(
        ...,
        description="Итого пассивы + капитал"
    )

    is_balanced: bool = Field(
        ...,
        description="Верно ли: Активы = Пассивы + Капитал"
    )


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTY CRUD
# ─────────────────────────────────────────────────────────────────────────────


class CounterpartyCreate(BaseModel):
    name:          str            = Field(..., min_length=1, max_length=255)
    inn:           Optional[str]  = Field(None, max_length=12)
    kpp:           Optional[str]  = Field(None, max_length=9)
    is_customer:   bool           = False
    is_supplier:   bool           = False
    contact_email: Optional[str]  = None
    contact_phone: Optional[str]  = None
    notes:         Optional[str]  = None


class CounterpartyUpdate(BaseModel):
    name:          Optional[str]  = Field(None, min_length=1, max_length=255)
    inn:           Optional[str]  = Field(None, max_length=12)
    kpp:           Optional[str]  = Field(None, max_length=9)
    is_customer:   Optional[bool] = None
    is_supplier:   Optional[bool] = None
    contact_email: Optional[str]  = None
    contact_phone: Optional[str]  = None
    notes:         Optional[str]  = None
    is_active:     Optional[bool] = None


class CounterpartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            UUID
    company_id:    UUID
    name:          str
    inn:           Optional[str]
    kpp:           Optional[str]
    is_customer:   bool
    is_supplier:   bool
    is_active:     bool
    contact_email: Optional[str]
    contact_phone: Optional[str]
    notes:         Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# INVOICE CRUD
# ─────────────────────────────────────────────────────────────────────────────


class InvoiceCreate(BaseModel):
    counterparty_id: UUID
    number:          str     = Field(..., min_length=1, max_length=100)
    date:            date
    due_date:        Optional[date]   = None
    total_amount:    Decimal = Field(..., gt=0)
    invoice_type:    str     = Field(..., description="customer_invoice | supplier_bill")
    description:     Optional[str]   = None
    currency:        str              = "RUB"


class InvoicePaymentRequest(BaseModel):
    """Запись частичной или полной оплаты счёта."""
    payment_amount: Decimal = Field(..., gt=0, description="Сумма оплаты")
    payment_date:   date


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                  UUID
    company_id:          UUID
    counterparty_id:     UUID
    counterparty_name:   Optional[str]   = None
    number:              str
    date:                date
    due_date:            Optional[date]
    total_amount:        DecimalStr
    paid_amount:         DecimalStr
    outstanding_amount:  DecimalStr
    status:              str
    invoice_type:        str
    description:         Optional[str]
    currency:            str


# ─────────────────────────────────────────────────────────────────────────────
# ДОЛГИ КОНТРАГЕНТОВ
# ─────────────────────────────────────────────────────────────────────────────


class CounterpartyDebt(BaseModel):
    """Задолженность одного контрагента."""
    counterparty_id:   UUID
    name:              str
    inn:               Optional[str]
    accounts_receivable: DecimalStr    # Дебиторка (нам должны)
    accounts_payable:    DecimalStr    # Кредиторка (мы должны)
    net_position:        DecimalStr    # AR − AP (>0 = нам должны)
    invoice_count:       int


class DebtSummaryResponse(BaseModel):
    """Сводная таблица долгов контрагентов."""
    as_of_date:                  date
    currency:                    str
    total_accounts_receivable:   DecimalStr    # Всего дебиторка
    total_accounts_payable:      DecimalStr    # Всего кредиторка
    net_position:                DecimalStr    # AR − AP итого
    top_debtors:                 list[CounterpartyDebt]    # Топ по дебиторке
    top_creditors:               list[CounterpartyDebt]    # Топ по кредиторке
    all_debts:                   list[CounterpartyDebt]
