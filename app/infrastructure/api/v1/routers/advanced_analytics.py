"""Advanced analytics API with ML insights and caching."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.advanced_analytics_service import (
    predict_cash_flow_arima,
    detect_expense_anomalies,
    calculate_customer_lifetime_value,
    predict_revenue_churn,
)
from app.services.cache_service import get_cached_analytics, set_cached_analytics
from app.services.notification_gateway import send_external_alert, should_send_alert

router = APIRouter(prefix="/companies/{company_id}/analytics", tags=["Advanced Analytics"])


@router.get("/forecast/cashflow", status_code=200)
async def forecast_cashflow(
    company_id: UUID,
    months: int = Query(6, ge=1, le=24),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """ARIMA cash flow forecast for next N months with caching."""
    # Check cache first
    cache_key = f"forecast:cashflow:{months}"
    cached = await get_cached_analytics(company_id, cache_key)
    if cached:
        return cached

    # Calculate forecast
    result = await predict_cash_flow_arima(db, company_id, months)

    # Cache for 1 hour
    await set_cached_analytics(company_id, cache_key, result, ttl_seconds=3600)

    return result


@router.get("/anomalies/expenses", status_code=200)
async def detect_anomalies(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Detect expense anomalies using 3-sigma rule with caching and alerts."""
    # Check cache first
    cached = await get_cached_analytics(company_id, "anomalies:expenses")
    if cached:
        return cached

    # Calculate anomalies
    result = await detect_expense_anomalies(db, company_id)

    # Send alert if critical
    if result.get("anomalies"):
        for anomaly in result["anomalies"]:
            if await should_send_alert(anomaly.get("z_score", 0)):
                await send_external_alert(
                    db, company_id, "expense_anomaly",
                    {"z_score": anomaly["z_score"], "amount": anomaly["amount"]}
                )

    # Cache result for 10 minutes
    await set_cached_analytics(company_id, "anomalies:expenses", result, ttl_seconds=600)

    return result


@router.get("/clv/{counterparty_id}", status_code=200)
async def get_customer_lifetime_value(
    company_id: UUID,
    counterparty_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Calculate customer lifetime value with caching."""
    cache_key = f"clv:{counterparty_id}"
    cached = await get_cached_analytics(company_id, cache_key)
    if cached:
        return cached

    result = await calculate_customer_lifetime_value(db, company_id, counterparty_id)

    # Cache for 24 hours
    await set_cached_analytics(company_id, cache_key, result, ttl_seconds=86400)

    return result


@router.get("/churn-risk", status_code=200)
async def get_churn_risk(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Predict revenue churn risk with caching and alerts."""
    cached = await get_cached_analytics(company_id, "churn:risk")
    if cached:
        return cached

    result = await predict_revenue_churn(db, company_id)

    # Send alert if high/critical churn risk
    if result.get("churn_risk") in ["high", "critical"]:
        await send_external_alert(
            db, company_id, "churn_alert",
            {"churn_risk": result["churn_risk"], "growth_rate": result["growth_rate_m3"]}
        )

    # Cache for 2 hours
    await set_cached_analytics(company_id, "churn:risk", result, ttl_seconds=7200)

    return result
