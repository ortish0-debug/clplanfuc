"""Advanced alerts API."""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.alert_rules import AlertRule, TriggeredAlert
from app.domain.models.finance import Account, Transaction, TransactionType
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.alert_engine_service import check_and_trigger_alerts

router = APIRouter(prefix="/companies/{company_id}/alerts", tags=["Alerts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateAlertRuleRequest(BaseModel):
    metric_type: str
    threshold_value: float
    email_recipient: str


class UpdateAlertRuleRequest(BaseModel):
    is_active: Optional[bool] = None
    threshold_value: Optional[float] = None
    email_recipient: Optional[str] = None


class CheckAlertRequest(BaseModel):
    metric_type: str
    value: float


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rule_to_dict(rule: AlertRule, triggered_count: int = 0) -> dict:
    return {
        "id":              str(rule.id),
        "metric_type":     rule.metric_type,
        "threshold_value": float(rule.threshold_value),
        "email_recipient": rule.email_recipient,
        "is_active":       rule.is_active,
        "triggered_count": triggered_count,
    }


def _alert_to_dict(a: TriggeredAlert) -> dict:
    return {
        "id":              str(a.id),
        "rule_id":         str(a.rule_id) if a.rule_id else None,
        "metric_type":     a.metric_type,
        "threshold_value": float(a.threshold_value),
        "actual_value":    float(a.actual_value),
        "triggered_at":    a.triggered_at.isoformat(),
        "message":         a.message,
        "is_read":         a.is_read,
    }


# ── Rules CRUD ────────────────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all alert rules with triggered counts."""
    rules_res = await db.execute(
        select(AlertRule).where(AlertRule.company_id == company_id)
    )
    rules = rules_res.scalars().all()

    counts_res = await db.execute(
        select(TriggeredAlert.rule_id, func.count(TriggeredAlert.id))
        .where(TriggeredAlert.company_id == company_id)
        .group_by(TriggeredAlert.rule_id)
    )
    counts = {str(row[0]): row[1] for row in counts_res.all()}

    return {"rules": [_rule_to_dict(r, counts.get(str(r.id), 0)) for r in rules]}


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
        threshold_value=Decimal(str(request.threshold_value)),
        email_recipient=request.email_recipient,
        is_active=True,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _rule_to_dict(rule)


@router.patch("/rules/{rule_id}")
async def update_alert_rule(
    company_id: UUID,
    rule_id: UUID,
    request: UpdateAlertRuleRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Toggle active or update threshold/email."""
    res = await db.execute(
        select(AlertRule).where(
            and_(AlertRule.id == rule_id, AlertRule.company_id == company_id)
        )
    )
    rule = res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")

    if request.is_active is not None:
        rule.is_active = request.is_active
    if request.threshold_value is not None:
        rule.threshold_value = Decimal(str(request.threshold_value))
    if request.email_recipient is not None:
        rule.email_recipient = request.email_recipient

    await db.commit()
    await db.refresh(rule)
    return _rule_to_dict(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_alert_rule(
    company_id: UUID,
    rule_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete rule and its triggered alerts."""
    res = await db.execute(
        select(AlertRule).where(
            and_(AlertRule.id == rule_id, AlertRule.company_id == company_id)
        )
    )
    rule = res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")

    # Удаляем связанные алерты
    triggered_res = await db.execute(
        select(TriggeredAlert).where(TriggeredAlert.rule_id == rule_id)
    )
    for ta in triggered_res.scalars().all():
        await db.delete(ta)

    await db.delete(rule)
    await db.commit()


# ── Triggered Alerts ─────────────────────────────────────────────────────────

@router.get("/triggered")
async def list_triggered(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Last 50 triggered alerts for company."""
    res = await db.execute(
        select(TriggeredAlert)
        .where(TriggeredAlert.company_id == company_id)
        .order_by(TriggeredAlert.triggered_at.desc())
        .limit(50)
    )
    alerts = res.scalars().all()
    unread = sum(1 for a in alerts if not a.is_read)
    return {"alerts": [_alert_to_dict(a) for a in alerts], "unread_count": unread}


@router.post("/triggered/{alert_id}/read")
async def mark_read(
    company_id: UUID,
    alert_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark triggered alert as read."""
    res = await db.execute(
        select(TriggeredAlert).where(
            and_(
                TriggeredAlert.id == alert_id,
                TriggeredAlert.company_id == company_id,
            )
        )
    )
    alert = res.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Алерт не найден")
    alert.is_read = True
    await db.commit()
    return {"status": "ok", "id": str(alert_id)}


# ── Check-all ─────────────────────────────────────────────────────────────────

@router.post("/check-all")
async def check_all_metrics(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check all active metrics right now and record triggered alerts."""

    # 1. Текущий суммарный баланс счетов
    bal_res = await db.execute(
        select(func.sum(Account.current_balance)).where(
            and_(Account.company_id == company_id, Account.is_deleted.is_(False))
        )
    )
    total_balance = float(bal_res.scalar() or 0)

    # 2. Расходы за текущий месяц
    today = date.today()
    month_start = date(today.year, today.month, 1)
    exp_res = await db.execute(
        select(func.sum(Transaction.amount)).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.payment_date >= month_start,
                Transaction.payment_date <= today,
                Transaction.is_deleted.is_(False),
            )
        )
    )
    monthly_expenses = float(exp_res.scalar() or 0)

    # 3. Прогоняем через движок алертов
    all_triggered = []
    for metric_type, value in [
        ("min_balance", total_balance),
        ("opex_spike",  monthly_expenses),
    ]:
        fired = await check_and_trigger_alerts(
            db, company_id, metric_type, value, write_triggered=True
        )
        all_triggered.extend(fired)

    if all_triggered:
        await db.commit()

    return {
        "checked":  2,
        "triggered": len(all_triggered),
        "balance":   total_balance,
        "expenses":  monthly_expenses,
        "alerts":    all_triggered,
    }


# ── Legacy endpoint (оставляем для совместимости) ─────────────────────────────

@router.post("/check-test", status_code=200)
async def test_alert_check(
    company_id: UUID,
    request: CheckAlertRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test alert rules (legacy)."""
    triggered = await check_and_trigger_alerts(
        db, company_id, request.metric_type, request.value
    )
    return {"triggered_count": len(triggered), "status": "success"}
