"""Predictive analytics & forecasting (Sprint 26)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType


async def predict_cash_flow_forecast(
    db: AsyncSession,
    company_id: UUID,
    days: int = 30,
) -> dict:
    """
    Predict cash flow 30 days ahead based on burn rate analysis.

    Returns: {
        "current_balance": float,
        "forecasted_balance": float,
        "burn_rate": float,  # avg daily expense
        "forecast_days": int,
        "cash_gap_risk": bool,  # True if balance goes negative
        "critical_date": str,  # when balance hits zero (ISO8601)
    }
    """
    # Get current balance
    acc_result = await db.execute(
        select(func.sum(Account.current_balance)).where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
            )
        )
    )
    current_balance = float(acc_result.scalar() or 0)

    # Calculate 30-day metrics
    month_ago = datetime.now(tz=timezone.utc) - timedelta(days=30)
    txn_result = await db.execute(
        select(
            func.sum(Transaction.amount_base_currency).label("expenses"),
        ).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.created_at >= month_ago,
            )
        )
    )
    expenses_30d = float(txn_result.scalar() or 0)
    burn_rate = expenses_30d / 30

    # Linear forecast
    forecasted_balance = current_balance - (burn_rate * days)
    cash_gap_risk = forecasted_balance < 0

    # Critical date calculation
    critical_date = None
    if cash_gap_risk and burn_rate > 0:
        days_to_zero = current_balance / burn_rate
        critical_date = (
            datetime.now(tz=timezone.utc) + timedelta(days=days_to_zero)
        ).isoformat()

    return {
        "current_balance": current_balance,
        "forecasted_balance": max(0, forecasted_balance),
        "burn_rate": burn_rate,
        "forecast_days": days,
        "cash_gap_risk": cash_gap_risk,
        "critical_date": critical_date,
    }
