"""
Pydantic v2 схемы для Заявок на оплату (PaymentRequest).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


class PaymentRequestCreate(BaseModel):
    category_id:  Optional[UUID]   = None
    project_id:   Optional[UUID]   = None
    amount:       Decimal          = Field(..., gt=0)
    planned_date: date
    description:  Optional[str]    = None


class PaymentRequestStatusUpdate(BaseModel):
    status: str = Field(
        ...,
        description="Новый статус: approved | rejected",
        pattern="^(approved|rejected)$",
    )
    rejection_reason: Optional[str] = Field(
        None,
        description="Причина отклонения (обязательна при rejected)",
    )


class PaymentRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    id:                UUID
    company_id:        UUID
    category_id:       Optional[UUID]
    project_id:        Optional[UUID]
    applicant_user_id: UUID
    reviewer_user_id:  Optional[UUID]
    amount:            DecimalStr
    planned_date:      date
    status:            str
    description:       Optional[str]
    rejection_reason:  Optional[str]
    reviewed_at:       Optional[datetime]
    created_at:        datetime
