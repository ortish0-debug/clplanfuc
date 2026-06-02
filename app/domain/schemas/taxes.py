"""Pydantic схемы для НДС (Sprint 17)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v.quantize(Decimal("0.01"))), return_type=str, when_used="json"),
]


class VatRecordResponse(BaseModel):
    """Запись реестра НДС."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_type: str = Field(..., description="Тип документа (invoice, accrual, etc.)")
    document_id: UUID
    vat_rate: str = Field(..., description="Ставка НДС (vat_0, vat_10, vat_20, vat_none)")
    base_amount: DecimalStr
    vat_amount: DecimalStr
    total_amount: DecimalStr
    is_input: bool = Field(..., description="True = входящий (к вычету), False = исходящий (к уплате)")
    operation_date: date


class VatSummaryResponse(BaseModel):
    """Агрегированная сводка НДС за период."""
    total_input_vat: DecimalStr = Field(..., description="Итого входящий НДС (к вычету)")
    total_output_vat: DecimalStr = Field(..., description="Итого исходящий НДС (к уплате)")
    net_vat_to_pay: DecimalStr = Field(..., description="Чистый НДС к уплате (output - input)")
