"""Advanced Prophet-like forecasting with seasonality and holidays."""
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Transaction
from app.services.cache_service import get_cached_analytics, set_cached_analytics

# Russian holidays (Российские праздники/выходные)
RU_HOLIDAYS = {
    "2026-01-01": "Новый год",
    "2026-01-05": "Новый год",
    "2026-02-23": "День защитника",
    "2026-03-08": "Международный женский день",
    "2026-05-01": "День труда",
    "2026-05-09": "День победы",
    "2026-11-04": "День народного единства",
}

# Weekends (Saturday=5, Sunday=6)
WEEKEND_BOOST = 1.15  # +15% volatility on weekends


async def generate_prophet_forecast(
    db: AsyncSession, company_id: UUID, periods: int = 90
) -> dict:
    """
    Prophet-like forecast: trend + seasonality + holidays + confidence intervals.
    """
    # Check cache first
    cache_key = f"forecast:prophet:{periods}"
    cached = await get_cached_analytics(company_id, cache_key)
    if cached:
        return cached

    # Get 365 days of historical data
    start_date = datetime.now() - timedelta(days=365)
    result = await db.execute(
        select(
            func.date(Transaction.created_at).label('date'),
            func.sum(Transaction.amount).label('daily_total'),
        ).where(
            Transaction.company_id == company_id,
            Transaction.created_at >= start_date,
        ).group_by('date').order_by('date')
    )

    history = result.all()
    if len(history) < 30:
        return {"status": "insufficient_data", "forecast": []}

    # Extract values
    values = [float(row[1] or 0) for row in history]
    dates = [row[0] for row in history]

    # Calculate trend (linear regression)
    n = len(values)
    x = list(range(n))
    mean_x = sum(x) / n
    mean_y = sum(values) / n
    numerator = sum((x[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    denominator = sum((x[i] - mean_x) ** 2 for i in range(n))
    slope = numerator / denominator if denominator > 0 else 0
    intercept = mean_y - slope * mean_x

    # Seasonality (7-day cycle)
    weekly_pattern = [0] * 7
    for i, val in enumerate(values[-49:]):  # Last 7 weeks
        dow = (i + 1) % 7
        weekly_pattern[dow] += val
    weekly_avg = sum(weekly_pattern) / 7
    seasonality = [v / weekly_avg if weekly_avg > 0 else 1 for v in weekly_pattern]

    # Generate forecast
    forecast = []
    last_date = datetime.strptime(str(dates[-1]), "%Y-%m-%d")
    last_value = values[-1]

    for i in range(1, periods + 1):
        future_date = last_date + timedelta(days=i)
        dow = future_date.weekday()

        # Base trend
        trend_value = slope * (n + i) + intercept

        # Seasonality
        seasonal_component = seasonality[dow if dow < 7 else 0]

        # Holiday effect (lower on holidays)
        holiday_factor = 0.8 if str(future_date.date()) in RU_HOLIDAYS else 1.0

        # Weekend boost
        weekend_factor = WEEKEND_BOOST if dow >= 5 else 1.0

        # Combined forecast
        predicted = trend_value * seasonal_component * holiday_factor * weekend_factor
        predicted = max(0, predicted)

        # Confidence intervals (±20%)
        upper_bound = predicted * 1.2
        lower_bound = predicted * 0.8

        forecast.append({
            "date": str(future_date.date()),
            "predicted": round(predicted, 2),
            "upper_ci": round(upper_bound, 2),
            "lower_ci": round(lower_bound, 2),
            "is_holiday": str(future_date.date()) in RU_HOLIDAYS,
        })

    result_data = {
        "status": "success",
        "company_id": str(company_id),
        "forecast_days": periods,
        "trend_slope": round(slope, 4),
        "seasonality": [round(s, 2) for s in seasonality],
        "holidays_count": sum(1 for f in forecast if f["is_holiday"]),
        "forecast": forecast,
    }

    # Cache for 24 hours
    await set_cached_analytics(company_id, cache_key, result_data, ttl_seconds=86400)

    return result_data
