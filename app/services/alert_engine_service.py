"""Alert engine service for threshold-based notifications."""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.alert_rules import AlertRule, TriggeredAlert


METRIC_LABELS = {
    "min_balance": "Минимальный баланс",
    "opex_spike":  "Всплеск расходов",
}


async def check_and_trigger_alerts(
    db: AsyncSession,
    company_id: UUID,
    metric_type: str,
    current_value: float,
    write_triggered: bool = False,
) -> list[dict]:
    """
    Check active alert rules for metric_type.
    Returns list of triggered rule dicts.
    If write_triggered=True — also persists TriggeredAlert records.
    """
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.company_id == company_id,
            AlertRule.metric_type == metric_type,
            AlertRule.is_active == True,
        )
    )
    rules = result.scalars().all()
    triggered = []

    for rule in rules:
        threshold = float(rule.threshold_value)
        fired = (
            (metric_type == "min_balance" and current_value < threshold) or
            (metric_type == "opex_spike"  and current_value > threshold)
        )
        if not fired:
            continue

        label = METRIC_LABELS.get(metric_type, metric_type)
        if metric_type == "min_balance":
            message = (
                f"Баланс {current_value:,.0f} ₽ ниже порога {threshold:,.0f} ₽"
            )
        else:
            message = (
                f"Расходы {current_value:,.0f} ₽ превысили порог {threshold:,.0f} ₽"
            )

        triggered.append({
            "rule_id":         str(rule.id),
            "metric_type":     metric_type,
            "threshold_value": threshold,
            "actual_value":    current_value,
            "message":         message,
        })

        if write_triggered:
            alert = TriggeredAlert(
                company_id=company_id,
                rule_id=rule.id,
                metric_type=metric_type,
                threshold_value=Decimal(str(threshold)),
                actual_value=Decimal(str(current_value)),
                message=message,
                is_read=False,
            )
            db.add(alert)

    return triggered
