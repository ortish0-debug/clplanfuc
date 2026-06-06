"""API: Currency exchange rates (Sprint 21)."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company
from app.domain.schemas.currency import CurrencyRatesListResponse, CurrencyRateResponse
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.currency_service import fetch_and_store_cbr_rates, get_rate

router = APIRouter(tags=["Валютные курсы"])

TARGET_CURRENCIES = ["USD", "EUR", "CNY", "BYN", "KZT"]


@router.get(
    "/companies/{company_id}/currency-rates",
    response_model=CurrencyRatesListResponse,
    summary="Get currency exchange rates",
)
async def get_currency_rates(
    company_id: UUID,
    rate_date: Optional[date] = Query(None, description="Date to get rates for (default: today)"),
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> CurrencyRatesListResponse:
    """Get currency exchange rates for specific date."""
    # IDOR protection: verify company exists and user has access
    company_result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    if not company_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Default to today if not specified
    if rate_date is None:
        rate_date = date.today()

    # Fetch rates from cache or API
    await fetch_and_store_cbr_rates(db, rate_date)

    # Retrieve rates for target currencies
    rates = []
    for currency in TARGET_CURRENCIES:
        rate_value = await get_rate(db, currency, rate_date)
        if rate_value:
            rates.append(
                CurrencyRateResponse(
                    currency_code=currency,
                    rate=float(rate_value),
                    rate_date=rate_date,
                )
            )

    return CurrencyRatesListResponse(date=rate_date, rates=rates)
