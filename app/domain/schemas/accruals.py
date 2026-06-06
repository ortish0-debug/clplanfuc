"""
Pydantic v2 схемы: Документы начислений и взаиморасчёты (Спринт 9).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer

# Decimal → строка для JSON (без потери точности)
DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v.quantize(Decimal("0.01"))), return_type=str, when_used="json"),
]


# ─────────────────────────────────────────────────────────────────────────────
# ACCRUAL DOCUMENT — CREATE / RESPONSE
# ─────────────────────────────────────────────────────────────────────────────


class AccrualDocumentCreate(BaseModel):
    counterparty_id: UUID  = Field(..., description="Контрагент-участник сделки")
    doc_type:        Literal["revenue", "expense"] = Field(
        ..., description="revenue — Реализация/Акт выставленный; expense — Накладная/Акт полученный"
    )
    doc_number:      str  = Field(..., min_length=1, max_length=128, description="Номер документа")
    doc_date:        date = Field(..., description="Дата документа (дата начисления)")
    amount:          Decimal = Field(..., gt=0, description="Сумма документа (> 0)")

    project_id:      Optional[UUID]    = Field(None, description="Проект (опционально)")
    category_id:     Optional[UUID]    = Field(None, description="Статья P&L (опционально)")
    contract_id:     Optional[UUID]    = Field(None, description="Договор/счёт-фактура (опционально)")
    description:     Optional[str]     = Field(None, max_length=2048, description="Описание / назначение платежа")


class AccrualDocumentResponse(BaseModel):
    id:              UUID
    company_id:      UUID
    counterparty_id: UUID
    doc_type:        str
    doc_number:      str
    doc_date:        date
    amount:          DecimalStr
    payment_status:  str
    project_id:      Optional[UUID]
    category_id:     Optional[UUID]
    contract_id:     Optional[UUID]
    description:     Optional[str]
    is_deleted:      bool
    created_at:      datetime
    updated_at:      datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION ACCRUAL LINK — REQUEST / RESPONSE
# ─────────────────────────────────────────────────────────────────────────────


class TransactionAccrualLinkRequest(BaseModel):
    transaction_id:      UUID    = Field(..., description="UUID транзакции-платежа")
    accrual_document_id: UUID    = Field(..., description="UUID документа начисления")
    amount:              Decimal = Field(..., gt=0, description="Сумма привязки (> 0)")


class TransactionAccrualLinkResponse(BaseModel):
    link_id:         UUID
    transaction_id:  UUID
    accrual_id:      UUID
    linked_amount:   DecimalStr
    new_doc_status:  str
    doc_remaining:   DecimalStr
    txn_remaining:   DecimalStr


class UnlinkResponse(BaseModel):
    accrual_id:     UUID
    new_doc_status: str
    doc_remaining:  DecimalStr


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERPARTY BALANCES — ВЕДОМОСТЬ ДОЛГОВ
# ─────────────────────────────────────────────────────────────────────────────


class CounterpartyBalanceResponse(BaseModel):
    counterparty_id:         UUID
    counterparty_name:       str
    counterparty_inn:        Optional[str]

    # Дебиторская задолженность (нам должны — REVENUE docs)
    receivable_total:        DecimalStr
    receivable_linked:       DecimalStr
    receivable_outstanding:  DecimalStr

    # Кредиторская задолженность (мы должны — EXPENSE docs)
    payable_total:           DecimalStr
    payable_linked:          DecimalStr
    payable_outstanding:     DecimalStr

    # Чистая позиция (> 0 = нам должны больше, < 0 = мы должны больше)
    net_position:            DecimalStr


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT CLOSURE — КАРТОЧКА ЗАКРЫТИЯ
# ─────────────────────────────────────────────────────────────────────────────


class DocumentClosureResponse(BaseModel):
    accrual_id:     UUID
    doc_number:     str
    doc_date:       date
    doc_type:       str
    total_amount:   DecimalStr
    total_linked:   DecimalStr
    remaining:      DecimalStr
    payment_status: str
    links:          list[dict]   # [{link_id, transaction_id, linked_amount, payment_date, description}]
