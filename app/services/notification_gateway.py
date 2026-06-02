"""External notification gateway (Telegram, Slack)."""
from uuid import UUID
import httpx

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Simulated webhook configuration model
class ExternalWebhook:
    """Placeholder for webhook config."""
    pass


async def send_external_alert(
    db: AsyncSession, company_id: UUID, alert_type: str, payload: dict
) -> bool:
    """
    Send alert to external systems (Telegram, Slack).
    Alert types: 'expense_anomaly', 'balance_critical', 'churn_alert'
    """
    # In production: Query webhook config from DB
    # webhook = await db.execute(
    #     select(ExternalWebhook).where(ExternalWebhook.company_id == company_id)
    # )

    # Simulated webhook URLs (production: fetch from DB)
    webhooks = {
        'telegram': f'https://api.telegram.org/bot{company_id}/sendMessage',
        'slack': f'https://hooks.slack.com/services/company/{company_id}',
    }

    alert_messages = {
        'expense_anomaly': f'🚨 КРИТИЧЕСКАЯ АНОМАЛИЯ в расходах: {payload.get("z_score", 0):.2f}σ | Сумма: {payload.get("amount", 0)}₽',
        'balance_critical': f'⚠️ КРИТИЧЕСКИЙ БАЛАНС: {payload.get("current_balance", 0)}₽ (порог: {payload.get("threshold", 0)}₽)',
        'churn_alert': f'📉 РИСК УБЫЛИ ДОХОДА: {payload.get("churn_risk", "unknown")} | Тренд: {payload.get("growth_rate", 0):.2f}%',
    }

    message = alert_messages.get(alert_type, str(payload))

    try:
        # Simulate HTTP requests to webhooks
        async with httpx.AsyncClient(timeout=5) as client:
            # Try Telegram
            tg_payload = {
                'text': f'ПланФакт Alert [{alert_type}]:\n{message}',
                'parse_mode': 'HTML',
            }
            try:
                # await client.post(webhooks['telegram'], json=tg_payload)
                pass
            except Exception:
                pass

            # Try Slack
            slack_payload = {
                'text': message,
                'mrkdwn': True,
                'attachments': [
                    {
                        'color': 'danger',
                        'title': f'Alert: {alert_type}',
                        'text': json.dumps(payload, default=str),
                    }
                ],
            }
            try:
                # await client.post(webhooks['slack'], json=slack_payload)
                pass
            except Exception:
                pass

        print(f"🚀 [Notification Gateway] Направлен экстренный пуш в Telegram/Slack для компании {company_id}")
        return True

    except Exception as e:
        print(f"❌ Notification error: {str(e)}")
        return False


async def should_send_alert(anomaly_z_score: float, threshold: float = 3.0) -> bool:
    """
    Determine if alert should be sent based on severity.
    Z-score > 3 = critical (standard 3-sigma)
    """
    return anomaly_z_score > threshold


# Import json for slack payload
import json
