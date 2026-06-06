"""Analytics API (Sprint 26+)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.analytics_service import predict_cash_flow_forecast

router = APIRouter(
    prefix="/companies/{company_id}/analytics",
    tags=["Analytics"],
)


class ForecastResponse(BaseModel):
    """Cash flow forecast response."""

    current_balance: float
    forecasted_balance: float
    burn_rate: float
    forecast_days: int
    cash_gap_risk: bool
    critical_date: str = None


@router.get("/forecast", response_model=ForecastResponse, status_code=200)
async def get_forecast(
    company_id: UUID,
    days: int = Query(30, ge=1, le=365),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> ForecastResponse:
    """Get 30/60/90-day cash flow forecast."""
    result = await predict_cash_flow_forecast(db, company_id, days)
    return ForecastResponse(**result)
