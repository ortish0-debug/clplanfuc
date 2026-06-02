"""Billing webhooks and payment processing (Sprint 23)."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.saas import Subscription
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.audit_service import log_audit_action

router = APIRouter(
    prefix="/billing",
    tags=["Billing & Payments"],
)


@router.post("/webhook/stripe", status_code=200)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook receiver for Stripe payment events.
    Stub implementation: extend with signature verification in production.
    """
    body = await request.json()
    event_type = body.get("type", "")
    data = body.get("data", {}).get("object", {})

    if event_type == "payment_intent.succeeded":
        stripe_customer_id = data.get("customer")

        if not stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing customer ID in event",
            )

        # Find subscription by stripe_customer_id
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_customer_id == stripe_customer_id
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription not found for customer {stripe_customer_id}",
            )

        # Extend subscription by 30 days
        subscription.end_date = datetime.now(tz=timezone.utc) + timedelta(days=30)

        await db.flush()

        # Audit log
        await log_audit_action(
            db=db,
            company_id=subscription.company_id,
            user_id=None,
            action="billing.payment_received",
            target_type="Subscription",
            target_id=str(subscription.id),
            ip_address=request.client.host if request.client else "unknown",
        )

        await db.commit()

    return {"status": "ok"}


class RefundRequest(BaseModel):
    """Refund request schema."""

    subscription_id: UUID
    amount: float


@router.post("/companies/{company_id}/billing/refund", status_code=200)
async def process_refund(
    company_id: UUID,
    body: RefundRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Process subscription refund."""
    result = await db.execute(
        select(Subscription).where(Subscription.id == body.subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if subscription.company_id != company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    subscription.status = "refunded"
    await db.flush()

    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="billing.refund_processed",
        target_type="Subscription",
        target_id=str(subscription.id),
        ip_address="unknown",
    )

    await db.commit()
    return {"status": "success", "refund_amount": body.amount}


@router.post("/webhook/yoomoney", status_code=200)
async def yoomoney_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Webhook receiver for YooMoney (Yandex.Kassa) payment events.
    Stub implementation: extend with signature verification in production.
    """
    body = await request.json()
    event_type = body.get("event", "")

    if event_type == "payment.succeeded":
        payment_id = body.get("object", {}).get("id")

        if not payment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing payment ID in event",
            )

        # Find subscription by yoomoney_payment_id
        result = await db.execute(
            select(Subscription).where(
                Subscription.yoomoney_payment_id == payment_id
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subscription not found for payment {payment_id}",
            )

        # Activate subscription
        subscription.status = "active"
        subscription.start_date = datetime.now(tz=timezone.utc)
        subscription.end_date = datetime.now(tz=timezone.utc) + timedelta(days=30)

        await db.flush()

        # Audit log
        await log_audit_action(
            db=db,
            company_id=subscription.company_id,
            user_id=None,
            action="billing.payment_received",
            target_type="Subscription",
            target_id=str(subscription.id),
            ip_address=request.client.host if request.client else "unknown",
        )

        await db.commit()

    return {"status": "ok"}


class RefundRequest(BaseModel):
    """Refund request schema."""

    subscription_id: UUID
    amount: float


@router.post("/companies/{company_id}/billing/refund", status_code=200)
async def process_refund(
    company_id: UUID,
    body: RefundRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Process subscription refund."""
    result = await db.execute(
        select(Subscription).where(Subscription.id == body.subscription_id)
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if subscription.company_id != company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    subscription.status = "refunded"
    await db.flush()

    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="billing.refund_processed",
        target_type="Subscription",
        target_id=str(subscription.id),
        ip_address="unknown",
    )

    await db.commit()
    return {"status": "success", "refund_amount": body.amount}
