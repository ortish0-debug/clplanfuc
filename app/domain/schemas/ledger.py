"""Pydantic v2 схемы: План счетов и Журнал проводок (Спринт 14)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v.quantize(Decimal("0.01"))), return_type=str, when_used="json"),
]


class AccountChartResponse(BaseModel):
    id: UUID
    code: str
    name: str
    category: str
    is_active: bool
    model_config = {"from_attributes": True}


class LedgerLineResponse(BaseModel):
    id: UUID
    account_chart_id: UUID
    debit: DecimalStr
    credit: DecimalStr
    model_config = {"from_attributes": True}


class JournalEntryResponse(BaseModel):
    id: UUID
    operation_date: date
    description: str
    source_doc_type: Optional[str] = None
    source_doc_id: Optional[UUID] = None
    lines: list[LedgerLineResponse] = Field(default_factory=list)
    model_config = {"from_attributes": True}
