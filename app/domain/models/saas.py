"""
Доменный слой: SaaS-модели.
Таблицы: users, subscriptions, subscription_plans, user_company_roles.
Feature Flags хранятся как JSON в subscription_plans для гибкой тарификации.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.domain.models.finance import Company


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    """
    Роли пользователя внутри конкретной компании (RBAC).
    Порядок от наибольших привилегий к наименьшим:
      OWNER      → всё, включая биллинг
      ADMIN      → всё, кроме биллинга
      ACCOUNTANT → финансовые операции, импорт, отчёты
      VIEWER     → только чтение всех отчётов
      MANAGER    → дашборд + ДДС + P&L; без Баланса/Долгов/Команды
    """
    OWNER      = "owner"
    ADMIN      = "admin"
    ACCOUNTANT = "accountant"
    VIEWER     = "viewer"
    MANAGER    = "manager"      # Ограниченный просмотр (только операционные метрики)


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"        # Пробный период
    ACTIVE = "active"            # Активная подписка
    PAST_DUE = "past_due"        # Просрочена (ожидание оплаты)
    CANCELED = "canceled"        # Отменена
    PAUSED = "paused"            # Приостановлена
    EXPIRED = "expired"          # Истекла


class BillingInterval(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class InvitationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


# ---------------------------------------------------------------------------
# Базовые миксины
# ---------------------------------------------------------------------------


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# SubscriptionPlan  (тарифный план — системная справочная таблица)
# ---------------------------------------------------------------------------


class PlanFlags:
    """
    Строковые константы ключей feature_flags для SubscriptionPlan / Subscription.

    Использование (без риска опечаток):
        plan.get_flag(PlanFlags.CAN_USE_AI, default=False)
        subscription.get_limit(PlanFlags.MAX_ACCOUNTS, default=3)

    Числовые лимиты (get_limit → int):
        MAX_ACCOUNTS          — максимум расчётных счетов
        MAX_USERS             — максимум участников команды (включая OWNER)
        MAX_BANK_CONNECTIONS  — максимум подключённых банковских API
        MAX_PROJECTS          — максимум проектов

    Булевые флаги (is_feature_enabled → bool):
        CAN_USE_AI            — доступен ли ИИ-парсер транзакций
        CAN_SYNC_BANKS        — доступна ли банковская синхронизация DirectBank
        CAN_EXPORT_XLSX       — экспорт отчётов в Excel
        CAN_USE_MULTI_CURRENCY— мультивалютный учёт
        CAN_USE_BUDGETS       — модуль бюджетирования
        CAN_USE_ASSETS        — модуль основных средств
        CAN_USE_LOANS         — модуль кредитов и займов

    Строковые значения (get_flag → str):
        SUPPORT_PRIORITY      — приоритет поддержки: "standard" | "priority" | "dedicated"
    """
    # Числовые лимиты
    MAX_ACCOUNTS:         str = "max_accounts"
    MAX_USERS:            str = "max_users"
    MAX_BANK_CONNECTIONS: str = "max_bank_connections"
    MAX_PROJECTS:         str = "max_projects"

    # Булевые флаги доступа к функциям
    CAN_USE_AI:             str = "can_use_ai"
    CAN_SYNC_BANKS:         str = "can_sync_banks"
    CAN_EXPORT_XLSX:        str = "can_export_xlsx"
    CAN_USE_MULTI_CURRENCY: str = "can_use_multi_currency"
    CAN_USE_BUDGETS:        str = "can_use_budgets"
    CAN_USE_ASSETS:         str = "can_use_assets"
    CAN_USE_LOANS:          str = "can_use_loans"

    # Строковые значения
    SUPPORT_PRIORITY: str = "support_priority"

    # Эталонные конфигурации для каждого тарифа (для сидинга БД)
    DEFAULTS: dict[str, dict] = {
        "free": {
            MAX_ACCOUNTS: 3,       MAX_USERS: 2,
            MAX_BANK_CONNECTIONS: 0, MAX_PROJECTS: 1,
            CAN_USE_AI: False,     CAN_SYNC_BANKS: False,
            CAN_EXPORT_XLSX: False, CAN_USE_MULTI_CURRENCY: False,
            CAN_USE_BUDGETS: False, CAN_USE_ASSETS: False,
            CAN_USE_LOANS: False,  SUPPORT_PRIORITY: "standard",
        },
        "starter": {
            MAX_ACCOUNTS: 5,       MAX_USERS: 5,
            MAX_BANK_CONNECTIONS: 1, MAX_PROJECTS: 5,
            CAN_USE_AI: True,      CAN_SYNC_BANKS: True,
            CAN_EXPORT_XLSX: True,  CAN_USE_MULTI_CURRENCY: False,
            CAN_USE_BUDGETS: True,  CAN_USE_ASSETS: False,
            CAN_USE_LOANS: False,  SUPPORT_PRIORITY: "standard",
        },
        "pro": {
            MAX_ACCOUNTS: 20,      MAX_USERS: 20,
            MAX_BANK_CONNECTIONS: 5, MAX_PROJECTS: 50,
            CAN_USE_AI: True,      CAN_SYNC_BANKS: True,
            CAN_EXPORT_XLSX: True,  CAN_USE_MULTI_CURRENCY: True,
            CAN_USE_BUDGETS: True,  CAN_USE_ASSETS: True,
            CAN_USE_LOANS: True,   SUPPORT_PRIORITY: "priority",
        },
        "enterprise": {
            MAX_ACCOUNTS: 999,     MAX_USERS: 999,
            MAX_BANK_CONNECTIONS: 50, MAX_PROJECTS: 999,
            CAN_USE_AI: True,      CAN_SYNC_BANKS: True,
            CAN_EXPORT_XLSX: True,  CAN_USE_MULTI_CURRENCY: True,
            CAN_USE_BUDGETS: True,  CAN_USE_ASSETS: True,
            CAN_USE_LOANS: True,   SUPPORT_PRIORITY: "dedicated",
        },
    }


class SubscriptionPlan(Base, TimestampMixin):
    """
    Тарифный план SaaS.
    Feature Flags хранятся в JSON — добавление новой функции
    не требует миграции схемы, только обновления записей в таблице.

    Все ключи флагов определены в PlanFlags — используй их вместо строк.

    Пример feature_flags (тариф 'pro'):
    {
        "can_use_ai": true,           // ИИ-парсер транзакций
        "max_accounts": 20,           // счетов в компании
        "max_users": 20,              // участников команды
        "max_bank_connections": 5,    // подключённых банков (DirectBank)
        "max_projects": 50,           // проектов
        "can_sync_banks": true,       // прямая банковская синхронизация
        "can_export_xlsx": true,      // экспорт в Excel
        "can_use_multi_currency": true,
        "can_use_budgets": true,
        "can_use_assets": true,       // ОС и амортизация
        "can_use_loans": true,        // кредиты и займы
        "support_priority": "priority"
    }
    """

    __tablename__ = "subscription_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Системный slug: "free", "starter", "pro", "enterprise"
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Цены в копейках/центах (целочисленная арифметика, без float)
    price_monthly_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_quarterly_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_annual_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(3), default="RUB", nullable=False
    )

    feature_flags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Флаг "публично отображается на странице тарифов"
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Связи
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="plan"
    )

    def get_flag(self, key: str, default=None):
        """Типобезопасное чтение feature flag с дефолтным значением."""
        return self.feature_flags.get(key, default)

    def __repr__(self) -> str:
        return f"<SubscriptionPlan slug={self.slug!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    """
    Пользователь платформы. Один пользователь может состоять в нескольких компаниях
    с разными ролями (связь через UserCompanyRole).
    Пароль никогда не хранится в открытом виде — только bcrypt-хэш.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(72), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Предпочитаемая локаль пользователя (ru, en и т.д.)
    locale: Mapped[str] = mapped_column(String(10), default="ru", nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", nullable=False
    )
    totp_secret: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Связи
    company_roles: Mapped[list["UserCompanyRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    invitations_sent: Mapped[list["CompanyInvitation"]] = relationship(
        foreign_keys="CompanyInvitation.invited_by_user_id",
        back_populates="invited_by",
    )

    __table_args__ = (
        Index("ix_users_email", "email"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


class Subscription(Base, TimestampMixin):
    """
    Активная подписка компании на тарифный план.
    Одна компания может иметь только одну активную подписку в каждый момент времени.
    История подписок сохраняется (записи не удаляются).

    Snapshot feature_flags копируется из плана на момент активации —
    защита от ретроактивного изменения условий при апгрейде плана.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum"),
        default=SubscriptionStatus.TRIALING,
        nullable=False,
    )
    billing_interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval, name="billing_interval_enum"),
        default=BillingInterval.MONTHLY,
        nullable=False,
    )

    # Временные рамки подписки
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Внешние ID платёжной системы (Stripe, ЮKassa и т.д.)
    external_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    external_customer_id: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    # Снимок feature flags на момент активации подписки
    feature_flags_snapshot: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )

    # Связи
    company: Mapped["Company"] = relationship(back_populates="subscriptions")
    plan: Mapped["SubscriptionPlan"] = relationship(back_populates="subscriptions")

    __table_args__ = (
        Index("ix_subscriptions_company_id", "company_id"),
        Index("ix_subscriptions_status", "status"),
        Index("ix_subscriptions_company_status", "company_id", "status"),
    )

    def is_feature_enabled(self, flag: str) -> bool:
        """Проверяет feature flag из снимка, не обращаясь к плану повторно."""
        return bool(self.feature_flags_snapshot.get(flag, False))

    def get_limit(self, flag: str, default: int = 0) -> int:
        """Возвращает числовой лимит из feature flags."""
        return int(self.feature_flags_snapshot.get(flag, default))

    def __repr__(self) -> str:
        return (
            f"<Subscription id={self.id} company_id={self.company_id} "
            f"status={self.status} plan_id={self.plan_id}>"
        )


# ---------------------------------------------------------------------------
# UserCompanyRole  (RBAC — связующая таблица пользователь × компания)
# ---------------------------------------------------------------------------


class UserCompanyRole(Base, TimestampMixin):
    """
    Роль пользователя в конкретной компании.
    Один пользователь может быть OWNER в одной компании и VIEWER в другой.

    Разрешения по ролям:
    - OWNER:       чтение/запись всего + управление биллингом
    - ADMIN:       чтение/запись всего + управление пользователями
    - ACCOUNTANT:  чтение/запись финансовых операций, без настроек компании
    - VIEWER:      только чтение отчётов и данных
    """

    __tablename__ = "user_company_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Дополнительные кастомные разрешения поверх роли (расширение RBAC)
    custom_permissions: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )
    invited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    joined_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Связи
    user: Mapped["User"] = relationship(back_populates="company_roles")
    company: Mapped["Company"] = relationship(back_populates="roles")

    __table_args__ = (
        # Один пользователь — одна роль в одной компании
        UniqueConstraint("user_id", "company_id", name="uq_user_company_roles"),
        Index("ix_user_company_roles_user_id", "user_id"),
        Index("ix_user_company_roles_company_id", "company_id"),
    )

    def can_write(self) -> bool:
        """True если роль позволяет создавать/редактировать финансовые операции."""
        return self.is_active and self.role in (
            UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT
        )

    def can_manage_users(self) -> bool:
        """True если роль позволяет управлять участниками компании."""
        return self.is_active and self.role in (UserRole.OWNER, UserRole.ADMIN)

    def can_manage_billing(self) -> bool:
        """True если роль позволяет управлять подпиской и биллингом."""
        return self.is_active and self.role == UserRole.OWNER

    def __repr__(self) -> str:
        return (
            f"<UserCompanyRole user_id={self.user_id} "
            f"company_id={self.company_id} role={self.role}>"
        )


# ---------------------------------------------------------------------------
# CompanyInvitation  (токен приглашения нового участника)
# ---------------------------------------------------------------------------


class CompanyInvitation(Base, TimestampMixin):
    """
    Одноразовый токен для приглашения пользователя в компанию.
    После принятия — создаётся запись UserCompanyRole, токен инвалидируется.
    """

    __tablename__ = "company_invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, name="invitation_status_enum"),
        default=InvitationStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Связи
    invited_by: Mapped["User"] = relationship(
        foreign_keys=[invited_by_user_id], back_populates="invitations_sent"
    )

    __table_args__ = (
        Index("ix_company_invitations_token", "token"),
        Index("ix_company_invitations_email", "email"),
        Index("ix_company_invitations_company_id", "company_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<CompanyInvitation id={self.id} email={self.email!r} "
            f"company_id={self.company_id} status={self.status}>"
        )
