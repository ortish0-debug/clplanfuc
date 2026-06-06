"""
Pydantic v2 схемы: Холдинг и консолидация ВГО (Спринт 11).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.functional_serializers import PlainSerializer

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(
        lambda v: str(v.quantize(Decimal("0.01"))),
        return_type=str,
        when_used="json",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# ЗАПРОСЫ — создание ВГО-связей
# ─────────────────────────────────────────────────────────────────────────────


class IntraGroupTransactionLinkRequest(BaseModel):
    source_transaction_id:      UUID    = Field(..., description="UUID исходящего платежа (Юрлицо 1)")
    destination_transaction_id: UUID    = Field(..., description="UUID входящего платежа  (Юрлицо 2)")
    amount:                     Decimal = Field(..., gt=0, description="Сумма внутригрупповой операции, ₽")


class IntraGroupAccrualLinkRequest(BaseModel):
    source_accrual_id:      UUID    = Field(..., description="UUID Акта-реализации  (Юрлицо 1)")
    destination_accrual_id: UUID    = Field(..., description="UUID Акта-закупки     (Юрлицо 2)")
    amount:                 Decimal = Field(..., gt=0, description="Сумма внутригрупповой операции, ₽")


# ─────────────────────────────────────────────────────────────────────────────
# ОТВЕТ — итог создания ВГО-связи
# ─────────────────────────────────────────────────────────────────────────────


class IntraGroupLinkResponse(BaseModel):
    link_id:        UUID
    source_id:      UUID
    destination_id: UUID
    linked_amount:  DecimalStr
    link_type:      str   # "transaction" | "accrual"


# ─────────────────────────────────────────────────────────────────────────────
# КОНСОЛИДИРОВАННЫЙ ДДС
# ─────────────────────────────────────────────────────────────────────────────


class ConsolidatedDDSResponse(BaseModel):
    """
    Консолидированный отчёт ДДС холдинга.

    Грязный оборот = весь денежный поток, включая внутригрупповые переводы.
    Чистый оборот  = только операции с внешними контрагентами (is_intra_group=False).
    Исключено ВГО  = dirty − clean (сумма элиминированных внутрихолдинговых переводов).
    """
    company_ids: list[UUID]
    start_date:  date
    end_date:    date

    # Грязный оборот (с ВГО)
    dirty_income:  DecimalStr
    dirty_expense: DecimalStr
    dirty_net:     DecimalStr

    # Консолидированный (без ВГО)
    clean_income:  DecimalStr
    clean_expense: DecimalStr
    clean_net:     DecimalStr

    # Исключённые ВГО
    eliminated_income:  DecimalStr
    eliminated_expense: DecimalStr


# ─────────────────────────────────────────────────────────────────────────────
# КОНСОЛИДИРОВАННЫЙ P&L
# ─────────────────────────────────────────────────────────────────────────────


class ConsolidatedPnLResponse(BaseModel):
    """
    Консолидированный P&L по методу начислений.

    Грязный P&L  = всe Акты, включая внутрихолдинговые реализации.
    Чистый P&L   = только внешние Акты (is_intra_group=False).
    Исключено ВГО = dirty − clean.
    """
    company_ids: list[UUID]
    start_date:  date
    end_date:    date

    # Грязный P&L (с ВГО)
    dirty_revenue: DecimalStr
    dirty_expense: DecimalStr
    dirty_profit:  DecimalStr

    # Консолидированный (без ВГО)
    clean_revenue: DecimalStr
    clean_expense: DecimalStr
    clean_profit:  DecimalStr

    # Исключённые ВГО
    eliminated_revenue: DecimalStr
    eliminated_expense: DecimalStr
