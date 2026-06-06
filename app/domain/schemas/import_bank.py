"""
Pydantic v2 схемы для импорта банковских выписок и управления AutoRule.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]

# ─────────────────────────────────────────────────────────────────────────────
# ПРЕДПРОСМОТР ИМПОРТА
# ─────────────────────────────────────────────────────────────────────────────


class ParsedTransactionPreview(BaseModel):
    """Одна распознанная транзакция из выписки — для показа на фронтенде."""
    doc_number:              Optional[str]
    payment_date:            date
    amount:                  DecimalStr
    transaction_type:        Literal["income", "expense"]
    counterparty_name:       Optional[str] = None
    counterparty_inn:        Optional[str] = None
    counterparty_account:    Optional[str] = None
    description:             str
    # Результат движка правил
    suggested_category_id:   Optional[UUID] = None
    suggested_category_name: Optional[str]  = None
    rule_name:               Optional[str]  = None


class BankStatementPreviewResponse(BaseModel):
    """Ответ эндпоинта POST /import/1c — предпросмотр без сохранения."""
    our_account:     Optional[str]
    bank_name:       Optional[str]
    period_from:     Optional[date]
    period_to:       Optional[date]
    opening_balance: Optional[DecimalStr]
    closing_balance: Optional[DecimalStr]

    total_count:     int
    income_count:    int
    expense_count:   int
    total_income:    DecimalStr
    total_expense:   DecimalStr
    rules_applied:   int = 0          # сколько транзакций получили категорию

    transactions: list[ParsedTransactionPreview]


# ─────────────────────────────────────────────────────────────────────────────
# ПОДТВЕРЖДЕНИЕ ИМПОРТА
# ─────────────────────────────────────────────────────────────────────────────


class ConfirmTransactionItem(BaseModel):
    """Одна транзакция для записи в БД при подтверждении импорта."""
    doc_number:       Optional[str]  = None
    payment_date:     date
    accrual_date:     Optional[date] = None
    amount:           Decimal        = Field(..., gt=0)
    transaction_type: Literal["income", "expense"]
    counterparty:     Optional[str]  = None
    counterparty_inn: Optional[str]  = None
    description:      str            = Field(..., max_length=500)
    category_id:      Optional[UUID] = None
    bank_transaction_id: Optional[str] = None   # для идемпотентности


class ConfirmImportRequest(BaseModel):
    """Тело POST /import/1c/confirm."""
    account_id:   UUID
    transactions: list[ConfirmTransactionItem] = Field(..., min_length=1, max_length=500)


class ConfirmImportResponse(BaseModel):
    imported_count:   int
    skipped_count:    int   # пропущено из-за дубликатов (bank_transaction_id)
    total_income:     DecimalStr
    total_expense:    DecimalStr


# ─────────────────────────────────────────────────────────────────────────────
# AutoRule — CRUD схемы
# ─────────────────────────────────────────────────────────────────────────────


class AutoRuleCreate(BaseModel):
    name:                  str  = Field(..., min_length=1, max_length=255)
    field_to_match:        str  = Field(..., description="description | counterparty_inn | counterparty_name")
    match_type:            str  = Field(default="contains", description="contains | regex | exact")
    pattern:               str  = Field(..., min_length=1, max_length=512)
    suggested_category_id: Optional[UUID] = None
    priority:              int  = Field(default=0, ge=0, le=1000)
    is_active:             bool = True


class AutoRuleUpdate(BaseModel):
    name:                  Optional[str]  = Field(None, min_length=1, max_length=255)
    field_to_match:        Optional[str]  = None
    match_type:            Optional[str]  = None
    pattern:               Optional[str]  = Field(None, min_length=1, max_length=512)
    suggested_category_id: Optional[UUID] = None
    priority:              Optional[int]  = Field(None, ge=0, le=1000)
    is_active:             Optional[bool] = None


class AutoRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                    UUID
    company_id:            UUID
    name:                  str
    field_to_match:        str
    match_type:            str
    pattern:               str
    suggested_category_id: Optional[UUID]
    priority:              int
    is_active:             bool
