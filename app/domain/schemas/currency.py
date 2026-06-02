"""Pydantic schemas for currency rates (Sprint 21)."""
from datetime import date

from pydantic import BaseModel, Field


class CurrencyRateResponse(BaseModel):
    """Single currency rate response."""
    currency_code: str = Field(..., description="Currency code (USD, EUR, CNY, BYN, KZT)")
    rate: float = Field(..., description="Exchange rate to RUB")
    rate_date: date = Field(..., description="Date of the rate")


class CurrencyRatesListResponse(BaseModel):
    """List of currency rates for a specific date."""
    rate_date: date = Field(..., description="Date of rates", alias="date")
    rates: list[CurrencyRateResponse] = Field(..., description="List of currency rates")
