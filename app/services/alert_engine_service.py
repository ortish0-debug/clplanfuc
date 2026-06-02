"""Alert engine service for threshold-based notifications."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.alert_rules import AlertRule


async def check_and_trigger_alerts(
    db: AsyncSession, company_id: UUID, metric_type: str, current_value: float
) -> int:
    """
    Check active alert rules and trigger notifications.

    Returns count of triggered alerts.
    """
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.company_id == company_id,
            AlertRule.metric_type == metric_type,
            AlertRule.is_active == True,
        )
    )
    rules = result.scalars().all()
    triggered_count = 0

    for rule in rules:
        threshold = float(rule.threshold_value)

        if (metric_type == 'min_balance' and current_value < threshold) or \
           (metric_type == 'opex_spike' and current_value > threshold):

            triggered_count += 1
            print(f"🔔 Alert triggered: {metric_type}={current_value}, threshold={threshold}, email={rule.email_recipient}")
            # Simulate email sending
            print(f"   Sending notification to {rule.email_recipient}")

    return triggered_count
