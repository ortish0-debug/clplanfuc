"""Pydantic схемы для основных средств (Sprint 18)."""
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


class FixedAssetCreate(BaseModel):
    """Запрос на добавление основного средства."""
    name: str = Field(..., min_length=1, max_length=255)
    inventory_number: str = Field(..., min_length=1, max_length=64, description="Уникальный инвентарный номер")
    initial_cost: Decimal = Field(..., gt=0, description="Первоначальная стоимость")
    purchase_date: date
    lifespan_months: int = Field(..., gt=0, description="Срок полезного использования (месяцы)")
    account_chart_id: Optional[UUID] = Field(None, description="Привязка к счету учета (опционально)")


class FixedAssetResponse(BaseModel):
    """Ответ с данными основного средства."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    inventory_number: str
    initial_cost: DecimalStr
    purchase_date: date
    lifespan_months: int
    accumulated_depreciation: DecimalStr
    status: str = Field(..., description="active | depreciated | written_off")
