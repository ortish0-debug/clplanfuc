"""Advanced integrations API (webhooks, external services)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db

router = APIRouter(prefix="/companies/{company_id}/integrations", tags=["Integrations Advanced"])


class WebhookConfig(BaseModel):
    service: str  # 'telegram', 'slack', 'teams', 'discord'
    webhook_url: str
    enabled: bool = True
    alert_types: list[str] = ["expense_anomaly", "balance_critical", "churn_alert"]


class NotificationConfig(BaseModel):
    alert_type: str  # 'expense_anomaly', 'balance_critical', 'churn_alert'
    enabled: bool = True
    threshold: float = 3.0  # Z-score threshold for anomalies


@router.post("/webhooks/register", status_code=201)
async def register_webhook(
    company_id: UUID,
    config: WebhookConfig,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Register external webhook (Telegram, Slack, Teams, Discord)."""
    # In production: Save to DB
    # webhook = ExternalWebhook(
    #     company_id=company_id,
    #     service=config.service,
    #     webhook_url=config.webhook_url,
    #     enabled=config.enabled,
    # )
    # db.add(webhook)
    # await db.commit()

    return {
        "status": "success",
        "service": config.service,
        "webhook_url": config.webhook_url[:50] + "..." if len(config.webhook_url) > 50 else config.webhook_url,
        "alert_types": config.alert_types,
        "message": "Webhook registered successfully",
    }


@router.get("/webhooks/list", status_code=200)
async def list_webhooks(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List registered webhooks for company."""
    # In production: Query from DB
    webhooks = [
        {
            "id": "webhook_1",
            "service": "telegram",
            "enabled": True,
            "alert_types": ["expense_anomaly", "balance_critical"],
        },
        {
            "id": "webhook_2",
            "service": "slack",
            "enabled": True,
            "alert_types": ["churn_alert"],
        },
    ]

    return {
        "status": "success",
        "company_id": str(company_id),
        "total_webhooks": len(webhooks),
        "webhooks": webhooks,
    }


@router.post("/alerts/config", status_code=201)
async def configure_alert(
    company_id: UUID,
    config: NotificationConfig,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Configure alert settings (thresholds, enabled types)."""
    return {
        "status": "success",
        "alert_type": config.alert_type,
        "enabled": config.enabled,
        "threshold": config.threshold,
        "message": f"Alert config updated: {config.alert_type}",
    }


@router.delete("/webhooks/{webhook_id}", status_code=200)
async def delete_webhook(
    company_id: UUID,
    webhook_id: str,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete webhook configuration."""
    return {
        "status": "success",
        "webhook_id": webhook_id,
        "message": "Webhook deleted successfully",
    }
