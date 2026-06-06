"""Pydantic v2 схемы: Складской учёт (Sprint 13)."""
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


class WarehouseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=512)


class WarehouseResponse(BaseModel):
    id: UUID
    name: str
    location: Optional[str]
    model_config = {"from_attributes": True}


class StockItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)
    sku: Optional[str] = Field(None, max_length=128)
    unit: str = Field("шт.", max_length=32)


class StockItemResponse(BaseModel):
    id: UUID
    name: str
    sku: Optional[str]
    unit: str
    model_config = {"from_attributes": True}


class StockOperationRequest(BaseModel):
    warehouse_id: UUID
    stock_item_id: UUID
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    operation_date: date
    notes: Optional[str] = None


class StockOperationResponse(BaseModel):
    id: UUID
    operation_type: str
    quantity: DecimalStr
    unit_price: DecimalStr
    total_amount: DecimalStr
    batch_id: Optional[UUID]
    operation_date: date
    model_config = {"from_attributes": True}


class StockBalanceResponse(BaseModel):
    stock_item_id: UUID
    stock_item_name: str
    sku: Optional[str]
    warehouse_id: UUID
    warehouse_name: str
    quantity: DecimalStr
    total_value: DecimalStr
