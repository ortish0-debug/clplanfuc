"""Advanced alerts API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.alert_rules import AlertRule
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.alert_engine_service import check_and_trigger_alerts

router = APIRouter(prefix="/companies/{company_id}/alerts", tags=["Alerts"])


class CreateAlertRuleRequest(BaseModel):
    metric_type: str
    threshold_value: float
    email_recipient: str


class CheckAlertRequest(BaseModel):
    metric_type: str
    value: float


@router.post("/rules", status_code=201)
async def create_alert_rule(
    company_id: UUID,
    request: CreateAlertRuleRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create alert rule."""
    rule = AlertRule(
        company_id=company_id,
        metric_type=request.metric_type,
        threshold_value=request.threshold_value,
        email_recipient=request.email_recipient,
        is_active=True,
    )
    db.add(rule)
    await db.commit()
    return {"status": "success", "rule_id": str(rule.id)}


@router.post("/check-test", status_code=200)
async def test_alert_check(
    company_id: UUID,
    request: CheckAlertRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test alert rules."""
    triggered = await check_and_trigger_alerts(
        db, company_id, request.metric_type, request.value
    )
    await db.commit()
    return {"triggered_count": triggered, "status": "success"}
