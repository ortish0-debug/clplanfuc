"""
Сервис: Тарифные ограничения SaaS (Спринт 8).

Каждая функция `check_*` — «охранник» перед созданием ресурса.
Вызывается в роутере ДО выполнения бизнес-операции.

Стратегия при отсутствии активной подписки:
  → Используются лимиты тарифа 'free' из PlanFlags.DEFAULTS.
  Таким образом компания без подписки работает в бесплатном режиме,
  а не падает с необработанной ошибкой.

Учитываемые статусы подписки:
  ACTIVE, TRIALING           — полные права по плану
  PAST_DUE                   — grace-period: права сохраняются, но выводится предупреждение
  CANCELED, PAUSED, EXPIRED  — деградация до free-тарифа
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account
from app.domain.models.integrations import BankConnection, BankConnectionStatus
from app.domain.models.saas import (
    PlanFlags,
    Subscription,
    SubscriptionStatus,
    UserCompanyRole,
)

# Статусы, при которых тарифные права ещё действуют
_PAYING_STATUSES = frozenset({
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.PAST_DUE,
})


# ─────────────────────────────────────────────────────────────────────────────
# ЗАГРУЗКА ПОДПИСКИ
# ─────────────────────────────────────────────────────────────────────────────


async def _get_subscription(
    db:         AsyncSession,
    company_id: UUID,
) -> Optional[Subscription]:
    """
    Возвращает наиболее свежую активную подписку компании.
    None — если подписки нет или она отменена/истекла.
    """
    result = await db.execute(
        select(Subscription)
        .where(
            and_(
                Subscription.company_id == company_id,
                Subscription.status.in_(_PAYING_STATUSES),
            )
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _get_flags(sub: Optional[Subscription]) -> dict:
    """
    Извлекает feature_flags из snapshot подписки.
    При отсутствии подписки — возвращает defaults free-тарифа.
    """
    if sub is not None:
        return sub.feature_flags_snapshot or PlanFlags.DEFAULTS["free"]
    return PlanFlags.DEFAULTS["free"]


def _get_limit(flags: dict, key: str, default: int = 0) -> int:
    return int(flags.get(key, default))


def _get_flag(flags: dict, key: str, default: bool = False) -> bool:
    return bool(flags.get(key, default))


# ─────────────────────────────────────────────────────────────────────────────
# ПУБЛИЧНЫЕ GUARD-ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def check_account_limit(
    db:         AsyncSession,
    company_id: UUID,
) -> None:
    """
    Проверяет, не превышен ли лимит банковских счетов по тарифу.

    Считает все активные не-удалённые счета компании.
    При достижении лимита бросает HTTPException(402).

    Вызывать ПЕРЕД созданием нового Account.
    """
    sub   = await _get_subscription(db, company_id)
    flags = _get_flags(sub)
    limit = _get_limit(flags, PlanFlags.MAX_ACCOUNTS, default=3)

    current = await db.scalar(
        select(func.count())
        .select_from(Account)
        .where(
            and_(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                Account.is_active.is_(True),
            )
        )
    ) or 0

    if current >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Превышен лимит банковских счетов для вашего тарифа "
                f"({current}/{limit}). "
                "Перейдите на следующий тариф для расширения."
            ),
        )


async def check_team_limit(
    db:         AsyncSession,
    company_id: UUID,
) -> None:
    """
    Проверяет лимит участников команды (активных UserCompanyRole).

    Вызывать ПЕРЕД отправкой нового приглашения или добавлением пользователя.
    """
    sub   = await _get_subscription(db, company_id)
    flags = _get_flags(sub)
    limit = _get_limit(flags, PlanFlags.MAX_USERS, default=2)

    current = await db.scalar(
        select(func.count())
        .select_from(UserCompanyRole)
        .where(
            and_(
                UserCompanyRole.company_id == company_id,
                UserCompanyRole.is_active.is_(True),
            )
        )
    ) or 0

    if current >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Превышен лимит участников команды для вашего тарифа "
                f"({current}/{limit}). "
                "Перейдите на следующий тариф, чтобы добавить больше сотрудников."
            ),
        )


async def check_bank_sync_allowed(
    db:         AsyncSession,
    company_id: UUID,
) -> None:
    """
    Двойная проверка для DirectBank:
      1. Булевый флаг can_sync_banks — функция вообще доступна на тарифе.
      2. Числовой лимит max_bank_connections — не превышен ли счётчик.

    Вызывать ПЕРЕД созданием BankConnection.
    """
    sub   = await _get_subscription(db, company_id)
    flags = _get_flags(sub)

    # ── Шаг 1: доступна ли функция на тарифе ─────────────────────────────
    if not _get_flag(flags, PlanFlags.CAN_SYNC_BANKS, default=False):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "Прямая синхронизация с банком (DirectBank) недоступна "
                "на вашем текущем тарифе. "
                "Перейдите на тариф Starter или выше."
            ),
        )

    # ── Шаг 2: не превышен ли лимит подключений ──────────────────────────
    limit = _get_limit(flags, PlanFlags.MAX_BANK_CONNECTIONS, default=0)

    current = await db.scalar(
        select(func.count())
        .select_from(BankConnection)
        .where(
            and_(
                BankConnection.company_id == company_id,
                # DISCONNECTED не занимает «слот»
                BankConnection.status != BankConnectionStatus.DISCONNECTED,
            )
        )
    ) or 0

    if current >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Превышен лимит банковских подключений для вашего тарифа "
                f"({current}/{limit}). "
                "Отключите неиспользуемые подключения или перейдите на тариф Pro."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (для отображения в UI без блокировки)
# ─────────────────────────────────────────────────────────────────────────────


async def get_usage_summary(
    db:         AsyncSession,
    company_id: UUID,
) -> dict:
    """
    Возвращает текущее потребление ресурсов компании vs. лимиты тарифа.
    Используется для отображения в разделе Настройки → Тариф.

    Пример ответа:
    {
        "plan_slug": "pro",
        "accounts":         {"current": 3,  "limit": 20,  "percent": 15},
        "users":            {"current": 7,  "limit": 20,  "percent": 35},
        "bank_connections": {"current": 2,  "limit": 5,   "percent": 40},
        "features": {
            "can_use_ai":   true,
            "can_sync_banks": true,
            "can_use_assets": true,
            "can_use_loans":  true,
        },
    }
    """
    sub   = await _get_subscription(db, company_id)
    flags = _get_flags(sub)

    # Текущее потребление (параллельные запросы через gather не нужны — быстро)
    accounts_count = await db.scalar(
        select(func.count()).select_from(Account).where(
            and_(Account.company_id == company_id,
                 Account.is_deleted.is_(False), Account.is_active.is_(True))
        )
    ) or 0

    users_count = await db.scalar(
        select(func.count()).select_from(UserCompanyRole).where(
            and_(UserCompanyRole.company_id == company_id,
                 UserCompanyRole.is_active.is_(True))
        )
    ) or 0

    bank_count = await db.scalar(
        select(func.count()).select_from(BankConnection).where(
            and_(BankConnection.company_id == company_id,
                 BankConnection.status != BankConnectionStatus.DISCONNECTED)
        )
    ) or 0

    def _pct(current: int, limit: int) -> int:
        return min(100, round(current / limit * 100)) if limit > 0 else 100

    max_acc  = _get_limit(flags, PlanFlags.MAX_ACCOUNTS, 3)
    max_usr  = _get_limit(flags, PlanFlags.MAX_USERS, 2)
    max_bnk  = _get_limit(flags, PlanFlags.MAX_BANK_CONNECTIONS, 0)

    return {
        "plan_slug": sub.feature_flags_snapshot.get("_slug", "free") if sub else "free",
        "subscription_status": sub.status.value if sub else None,
        "accounts":         {"current": accounts_count, "limit": max_acc,  "percent": _pct(accounts_count, max_acc)},
        "users":            {"current": users_count,    "limit": max_usr,  "percent": _pct(users_count, max_usr)},
        "bank_connections": {"current": bank_count,     "limit": max_bnk,  "percent": _pct(bank_count, max_bnk)},
        "features": {
            PlanFlags.CAN_USE_AI:             _get_flag(flags, PlanFlags.CAN_USE_AI),
            PlanFlags.CAN_SYNC_BANKS:         _get_flag(flags, PlanFlags.CAN_SYNC_BANKS),
            PlanFlags.CAN_EXPORT_XLSX:        _get_flag(flags, PlanFlags.CAN_EXPORT_XLSX),
            PlanFlags.CAN_USE_MULTI_CURRENCY: _get_flag(flags, PlanFlags.CAN_USE_MULTI_CURRENCY),
            PlanFlags.CAN_USE_BUDGETS:        _get_flag(flags, PlanFlags.CAN_USE_BUDGETS),
            PlanFlags.CAN_USE_ASSETS:         _get_flag(flags, PlanFlags.CAN_USE_ASSETS),
            PlanFlags.CAN_USE_LOANS:          _get_flag(flags, PlanFlags.CAN_USE_LOANS),
        },
    }
