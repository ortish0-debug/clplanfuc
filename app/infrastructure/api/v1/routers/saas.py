"""
FastAPI роутер: SaaS-инфраструктура — тарифы и биллинг (Спринт 8).

Эндпоинты:
  GET  /companies/{company_id}/saas/usage   — текущее потребление vs лимиты
  POST /saas/webhook/stripe                 — симуляция Stripe webhook (оплата)
  GET  /saas/plans                          — публичный прайс-лист тарифов

Webhook /saas/webhook/stripe:
  В продакшене этот эндпоинт вызывается Stripe при успешной оплате.
  Заглушка принимает {company_id, plan_slug} и:
    1. Находит или создаёт SubscriptionPlan с указанным slug.
    2. Деактивирует текущую активную подписку компании (если есть).
    3. Создаёт новую Subscription со статусом ACTIVE и snapshot лимитов
       из PlanFlags.DEFAULTS[plan_slug].
  В продакшене перед обработкой нужна верификация Stripe-сигнатуры заголовка.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.saas import (
    BillingInterval,
    PlanFlags,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.saas_limit_service import get_usage_summary

router = APIRouter(tags=["SaaS — тарифы и биллинг"])

_VALID_SLUGS = frozenset(PlanFlags.DEFAULTS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# СХЕМЫ
# ─────────────────────────────────────────────────────────────────────────────


class ResourceUsage(BaseModel):
    current: int
    limit:   int
    percent: int


class UsageResponse(BaseModel):
    """Текущее потребление ресурсов компании vs лимиты тарифа."""
    plan_slug:            Optional[str]
    subscription_status:  Optional[str]
    accounts:             ResourceUsage
    users:                ResourceUsage
    bank_connections:     ResourceUsage
    features:             dict[str, bool]


class StripeWebhookRequest(BaseModel):
    company_id: UUID  = Field(..., description="UUID компании-плательщика")
    plan_slug:  str   = Field(
        ...,
        description="Тарифный план: free | starter | pro | enterprise",
        pattern="^(free|starter|pro|enterprise)$",
    )
    # В продакшене здесь добавятся: stripe_subscription_id, stripe_customer_id,
    # billing_interval (monthly/annual), period_start, period_end


class StripeWebhookResponse(BaseModel):
    status:             str
    subscription_id:    UUID
    plan_slug:          str
    is_upgrade:         bool
    features_activated: list[str]   # список флагов, которые стали True


class PlanResponse(BaseModel):
    """Публичный тарифный план для страницы прайсинга."""
    slug:              str
    name:              str
    description:       Optional[str]
    price_monthly:     int   # в копейках
    currency:          str
    max_accounts:      int
    max_users:         int
    max_bank_connections: int
    can_use_ai:        bool
    can_sync_banks:    bool
    can_use_budgets:   bool
    can_use_assets:    bool
    can_use_loans:     bool
    support_priority:  str


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_or_create_plan(
    db:   AsyncSession,
    slug: str,
) -> SubscriptionPlan:
    """Ищет план по slug, при отсутствии создаёт из PlanFlags.DEFAULTS."""
    result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.slug == slug)
    )
    plan = result.scalar_one_or_none()
    if plan:
        return plan

    defaults   = PlanFlags.DEFAULTS.get(slug, PlanFlags.DEFAULTS["free"])
    plan_names = {
        "free":       ("Бесплатный",   "Базовый план для старта"),
        "starter":    ("Стартер",      "Для малого бизнеса"),
        "pro":        ("Профессионал", "Полный набор функций"),
        "enterprise": ("Корпоративный","Безлимитный для холдингов"),
    }
    name, desc = plan_names.get(slug, (slug.capitalize(), None))

    price_map = {"free": 0, "starter": 99900, "pro": 299900, "enterprise": 999900}

    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        slug=slug,
        name=name,
        description=desc,
        price_monthly_cents=price_map.get(slug, 0),
        feature_flags={**defaults, "_slug": slug},
        is_public=True,
        is_active=True,
        sort_order=list(_VALID_SLUGS).index(slug) if slug in _VALID_SLUGS else 99,
    )
    db.add(plan)
    await db.flush()
    return plan


async def _deactivate_current_subscriptions(
    db:         AsyncSession,
    company_id: UUID,
) -> Optional[str]:
    """Отменяет все активные подписки компании. Возвращает slug старого плана."""
    result = await db.execute(
        select(Subscription, SubscriptionPlan.slug)
        .join(SubscriptionPlan, Subscription.plan_id == SubscriptionPlan.id)
        .where(
            and_(
                Subscription.company_id == company_id,
                Subscription.status.in_([
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.TRIALING,
                ]),
            )
        )
    )
    rows = result.all()
    old_slug = None
    for sub, slug in rows:
        sub.status     = SubscriptionStatus.CANCELED
        sub.canceled_at = datetime.now(tz=timezone.utc)
        old_slug = slug
    return old_slug


def _activated_features(old_flags: dict, new_flags: dict) -> list[str]:
    """Возвращает список булевых флагов, которые стали True при апгрейде."""
    return [
        k for k, v in new_flags.items()
        if isinstance(v, bool) and v and not old_flags.get(k, False)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# USAGE
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/saas/usage",
    response_model=UsageResponse,
    summary="Текущее потребление ресурсов vs лимиты тарифа",
    description=(
        "Показывает сколько счетов, пользователей и банковских подключений "
        "использует компания относительно лимитов активного тарифа. "
        "Доступно всем ролям — данные видны в разделе Настройки."
    ),
)
async def get_usage(
    company_id:   UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> UsageResponse:
    raw = await get_usage_summary(db=db, company_id=company_id)
    return UsageResponse(
        plan_slug=raw["plan_slug"],
        subscription_status=raw["subscription_status"],
        accounts=ResourceUsage(**raw["accounts"]),
        users=ResourceUsage(**raw["users"]),
        bank_connections=ResourceUsage(**raw["bank_connections"]),
        features={k: bool(v) for k, v in raw["features"].items()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# STRIPE WEBHOOK (заглушка / симуляция)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/saas/webhook/stripe",
    response_model=StripeWebhookResponse,
    summary="Webhook успешной оплаты (Stripe-симуляция)",
    description=(
        "Симулирует обработку Stripe webhook события `invoice.payment_succeeded`. "
        "В продакшене: верифицирует подпись Stripe-Signature, "
        "извлекает реальный subscription_id и обновляет данные подписки. "
        "Текущая заглушка принимает company_id + plan_slug и "
        "немедленно активирует соответствующий тариф."
    ),
)
async def stripe_webhook(
    body: StripeWebhookRequest,
    db:   AsyncSession = Depends(get_db),
    # В продакшене: stripe_signature: str = Header(None, alias="Stripe-Signature")
) -> StripeWebhookResponse:
    # ── 1. Находим / создаём тарифный план ───────────────────────────────
    plan = await _get_or_create_plan(db, body.plan_slug)
    new_flags = {**PlanFlags.DEFAULTS.get(body.plan_slug, {}), "_slug": body.plan_slug}

    # ── 2. Деактивируем текущую подписку ─────────────────────────────────
    old_slug = await _deactivate_current_subscriptions(db, body.company_id)
    old_flags = PlanFlags.DEFAULTS.get(old_slug, {}) if old_slug else {}
    is_upgrade = (old_slug or "free") != body.plan_slug

    # ── 3. Создаём новую активную подписку ───────────────────────────────
    now     = datetime.now(tz=timezone.utc)
    new_sub = Subscription(
        id=uuid.uuid4(),
        company_id=body.company_id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        billing_interval=BillingInterval.MONTHLY,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        # Snapshot сохраняет состояние тарифа на момент активации.
        # Даже если позже тариф изменится — пользователь получает то, за что заплатил.
        feature_flags_snapshot=new_flags,
    )
    db.add(new_sub)
    await db.flush()

    activated = _activated_features(old_flags, new_flags)

    return StripeWebhookResponse(
        status="activated",
        subscription_id=new_sub.id,
        plan_slug=body.plan_slug,
        is_upgrade=is_upgrade,
        features_activated=activated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ПРАЙС-ЛИСТ (публичный)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/saas/plans",
    response_model=list[PlanResponse],
    summary="Публичный прайс-лист тарифов",
    description="Не требует аутентификации. Используется на лендинге.",
)
async def list_plans(
    db: AsyncSession = Depends(get_db),
) -> list[PlanResponse]:
    result = await db.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order)
    )
    plans = list(result.scalars().all())

    # Если планов ещё нет — возвращаем статичные данные из PlanFlags.DEFAULTS
    if not plans:
        return _static_plans()

    return [_plan_to_response(p) for p in plans]


def _plan_to_response(p: SubscriptionPlan) -> PlanResponse:
    f = p.feature_flags
    return PlanResponse(
        slug=p.slug,
        name=p.name,
        description=p.description,
        price_monthly=p.price_monthly_cents,
        currency=p.currency_code,
        max_accounts=int(f.get(PlanFlags.MAX_ACCOUNTS, 0)),
        max_users=int(f.get(PlanFlags.MAX_USERS, 0)),
        max_bank_connections=int(f.get(PlanFlags.MAX_BANK_CONNECTIONS, 0)),
        can_use_ai=bool(f.get(PlanFlags.CAN_USE_AI, False)),
        can_sync_banks=bool(f.get(PlanFlags.CAN_SYNC_BANKS, False)),
        can_use_budgets=bool(f.get(PlanFlags.CAN_USE_BUDGETS, False)),
        can_use_assets=bool(f.get(PlanFlags.CAN_USE_ASSETS, False)),
        can_use_loans=bool(f.get(PlanFlags.CAN_USE_LOANS, False)),
        support_priority=str(f.get(PlanFlags.SUPPORT_PRIORITY, "standard")),
    )


def _static_plans() -> list[PlanResponse]:
    """Статичный прайс из PlanFlags.DEFAULTS — когда таблица ещё пуста."""
    price_map = {"free": 0, "starter": 99900, "pro": 299900, "enterprise": 999900}
    name_map  = {
        "free": "Бесплатный", "starter": "Стартер",
        "pro":  "Профессионал", "enterprise": "Корпоративный",
    }
    out = []
    for slug, flags in PlanFlags.DEFAULTS.items():
        out.append(PlanResponse(
            slug=slug, name=name_map.get(slug, slug),
            description=None, price_monthly=price_map.get(slug, 0), currency="RUB",
            max_accounts=flags[PlanFlags.MAX_ACCOUNTS],
            max_users=flags[PlanFlags.MAX_USERS],
            max_bank_connections=flags[PlanFlags.MAX_BANK_CONNECTIONS],
            can_use_ai=flags[PlanFlags.CAN_USE_AI],
            can_sync_banks=flags[PlanFlags.CAN_SYNC_BANKS],
            can_use_budgets=flags[PlanFlags.CAN_USE_BUDGETS],
            can_use_assets=flags[PlanFlags.CAN_USE_ASSETS],
            can_use_loans=flags[PlanFlags.CAN_USE_LOANS],
            support_priority=flags[PlanFlags.SUPPORT_PRIORITY],
        ))
    return out
