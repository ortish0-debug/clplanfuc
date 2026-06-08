"""
FastAPI роутер: система авторизации (регистрация, вход, JWT-токены).

Эндпоинты:
  POST /auth/register  — регистрация + создание компании
  POST /auth/token     — вход по email + пароль → JWT-токен
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.security.totp import generate_totp_secret, get_totp_uri, verify_totp_code
from app.services.audit_service import log_audit_action
from app.infrastructure.api.v1.dependencies.auth import CurrentUser, get_current_user

from app.domain.models.finance import Company, Currency
from app.domain.models.saas import (
    BillingInterval,
    CompanyInvitation,
    InvitationStatus,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
    UserCompanyRole,
    UserRole,
)
from app.domain.schemas.auth import RegisterRequest, RegisterResponse, TokenResponse
from app.infrastructure.database.session import get_db
from app.services.jwt_service import encode_token, TOKEN_EXPIRE_MINUTES, revoke_token

router = APIRouter(
    prefix="/auth",
    tags=["Авторизация"],
)


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


def _hash_password(plain: str) -> str:
    """Хэширует пароль через bcrypt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    """Проверяет пароль через bcrypt."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


async def _get_or_create_free_plan(db: AsyncSession) -> SubscriptionPlan:
    """Получает или создаёт тарифный план FREE для новых пользователей."""
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.slug == "free"))
    plan = result.scalar_one_or_none()
    if plan:
        return plan

    # Создаём FREE план
    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        slug="free",
        name="Бесплатный",
        description="Базовый план для старта",
        price_monthly_cents=0,
        price_quarterly_cents=0,
        price_annual_cents=0,
        currency_code="RUB",
        feature_flags={
            "can_use_ai": False,
            "max_accounts": 3,
            "max_users": 2,
            "can_sync_banks": False,
            "can_export_xlsx": False,
            "can_use_multi_currency": False,
            "can_use_budgets": False,
            "support_priority": "community",
        },
        is_public=True,
        is_active=True,
        sort_order=0,
    )
    db.add(plan)
    await db.flush()
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# РЕГИСТРАЦИЯ
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация нового пользователя",
    description="Создаёт пользователя и компанию по умолчанию. Пользователю присваивается роль OWNER в этой компании.",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """
    Регистрация нового пользователя.

    1. Проверяет, не существует ли уже user с этим email
    2. Хэширует пароль через bcrypt
    3. Создаёт User
    4. Создаёт новую Company для пользователя
    5. Присваивает роль OWNER
    6. Подписывает на FREE-план
    """
    # ── Проверка на существование user с этим email ────────────────────────
    existing_user = await db.execute(select(User).where(User.email == body.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Пользователь с email '{body.email}' уже зарегистрирован.",
        )

    # ── Создаём пользователя ────────────────────────────────────────────────
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
        is_active=True,
        is_verified=False,
        is_superuser=False,
        email_verified_at=None,
        locale="ru",
        timezone="Europe/Moscow",
    )
    db.add(user)
    await db.flush()

    # ── Создаём компанию ────────────────────────────────────────────────────
    company_name = body.company_name or f"{body.full_name}'s Company"
    from app.domain.models.finance import TaxRegime
    company = Company(
        id=uuid.uuid4(),
        name=company_name,
        legal_name=company_name,
        inn=None,  # Пользователь может заполнить позже
        currency=Currency.RUB,
        timezone="Europe/Moscow",
        tax_regime=TaxRegime(body.tax_regime),
        settings={
            "business_model": "standard",
            "currency": "RUB",
        },
        is_deleted=False,
    )
    db.add(company)
    await db.flush()

    # ── Присваиваем роль OWNER ──────────────────────────────────────────────
    role = UserCompanyRole(
        id=uuid.uuid4(),
        user_id=user.id,
        company_id=company.id,
        role=UserRole.OWNER,
        is_active=True,
        custom_permissions={},
        joined_at=datetime.now(tz=timezone.utc),
    )
    db.add(role)
    await db.flush()

    # ── Подписываем на FREE-план ────────────────────────────────────────────
    plan = await _get_or_create_free_plan(db)
    subscription = Subscription(
        id=uuid.uuid4(),
        company_id=company.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        billing_interval=BillingInterval.MONTHLY,
        feature_flags_snapshot=plan.feature_flags,
    )
    db.add(subscription)
    await db.flush()

    # Коммитим ДО отправки ответа: FastAPI отправляет HTTP-ответ раньше чем
    # cleanup get_db делает commit, поэтому немедленный логин после регистрации
    # видел бы пустую БД и получал 401.
    await db.commit()

    return RegisterResponse(
        user_id=user.id,
        company_id=company.id,
        email=user.email,
        full_name=user.full_name,
        tax_regime=company.tax_regime.value,
        message=f"Пользователь {user.email} и компания '{company_name}' успешно созданы.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ВХОД И ПОЛУЧЕНИЕ ТОКЕНА
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Получить JWT-токен",
    description="Вход по email и паролю. Возвращает JWT-токен доступа и информацию о пользователе.",
)
async def login(
    username: str = Form(...),
    password: str = Form(...),
    code: str = Form(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Вход пользователя и получение JWT-токена.

    1. Ищет user с этим email (username поле)
    2. Проверяет пароль через bcrypt
    3. Получает первую активную компанию (у которой есть роль)
    4. Кодирует JWT с user_id и company_id
    5. Логирует успешный вход или неудачную попытку в audit trail
    """
    ip_address = request.client.host if request.client else "unknown"

    # ── Ищем пользователя ───────────────────────────────────────────────────
    result = await db.execute(select(User).where(User.email == username))
    user = result.scalar_one_or_none()

    if not user:
        await log_audit_action(
            db=db,
            company_id=None,
            user_id=None,
            action="user.login_failed",
            target_type="user",
            target_id=None,
            ip_address=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Проверяем пароль ────────────────────────────────────────────────────
    if not _verify_password(password, user.hashed_password):
        await log_audit_action(
            db=db,
            company_id=None,
            user_id=user.id,
            action="user.login_failed",
            target_type="user",
            target_id=user.id,
            ip_address=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Проверяем 2FA если включена ──────────────────────────────────────────
    if user.is_2fa_enabled:
        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="2FA_REQUIRED",
            )
        if not verify_totp_code(user.totp_secret, code):
            await log_audit_action(
                db=db,
                company_id=None,
                user_id=user.id,
                action="user.login_failed_2fa",
                target_type="user",
                target_id=user.id,
                ip_address=ip_address,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 2FA code.",
            )

    # ── Получаем первую активную компанию пользователя ─────────────────────
    role_result = await db.execute(
        select(UserCompanyRole).where(
            and_(
                UserCompanyRole.user_id == user.id,
                UserCompanyRole.is_active.is_(True),
            )
        )
    )
    role = role_result.scalars().first()

    if not role:
        await log_audit_action(
            db=db,
            company_id=None,
            user_id=user.id,
            action="user.login_failed",
            target_type="user",
            target_id=user.id,
            ip_address=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="У пользователя нет активных компаний. Обратитесь к администратору.",
        )

    # ── Генерируем JWT-токен ────────────────────────────────────────────────
    expires_in = TOKEN_EXPIRE_MINUTES * 60
    token = encode_token(
        user_id=user.id,
        company_id=role.company_id,
        email=user.email,
        expires_in=expires_in,
    )

    # ── Логируем успешный вход ──────────────────────────────────────────────
    await log_audit_action(
        db=db,
        company_id=role.company_id,
        user_id=user.id,
        action="user.login",
        target_type="user",
        target_id=user.id,
        ip_address=ip_address,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        company_id=role.company_id,
        email=user.email,
        full_name=user.full_name,
        role=role.role.value,        # "owner" / "admin" / "accountant" / "viewer" / "manager"
        expires_in=expires_in,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2FA / TOTP
# ─────────────────────────────────────────────────────────────────────────────


class TotpSetupResponse(BaseModel):
    """Response for 2FA setup with secret and provisioning URI."""
    secret: str
    totp_uri: str


class TotpEnableRequest(BaseModel):
    """Request to enable 2FA with verification code."""
    code: str


@router.post(
    "/2fa/setup",
    response_model=TotpSetupResponse,
    summary="Setup 2FA",
    description="Generate TOTP secret and provisioning URI for QR code.",
)
async def setup_2fa(
    current_user: CurrentUser = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TotpSetupResponse:
    """Generate TOTP secret and URI for 2FA setup."""
    secret = generate_totp_secret()
    totp_uri = get_totp_uri(secret=secret, username=current_user.email)

    # ── Сохраняем секрет пока что без включения 2FA ──────────────────────
    user = await db.execute(select(User).where(User.id == current_user.id))
    user_obj = user.scalar_one()
    user_obj.totp_secret = secret
    await db.flush()

    return TotpSetupResponse(secret=secret, totp_uri=totp_uri)


@router.post(
    "/2fa/enable",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Enable 2FA",
    description="Verify TOTP code and enable 2FA protection.",
)
async def enable_2fa(
    body: TotpEnableRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify TOTP code and enable 2FA for current user."""
    user = await db.execute(select(User).where(User.id == current_user.id))
    user_obj = user.scalar_one()

    if not user_obj.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA setup not initiated. Call /auth/2fa/setup first.",
        )

    # ── Проверяем код ───────────────────────────────────────────────────
    if not verify_totp_code(user_obj.totp_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    # ── Включаем 2FA ─────────────────────────────────────────────────────
    user_obj.is_2fa_enabled = True
    await db.flush()

    # ── Логируем событие ────────────────────────────────────────────────
    ip_address = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=None,
        user_id=current_user.id,
        action="user.2fa_enabled",
        target_type="user",
        target_id=current_user.id,
        ip_address=ip_address,
    )

    await db.commit()
    return {"detail": "2FA successfully enabled."}


@router.get(
    "/invite-info",
    summary="Информация об инвайте",
    description="Возвращает данные приглашения по токену — компанию, роль, email. Не принимает инвайт.",
)
async def get_invite_info(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import datetime, timezone
    result = await db.execute(
        select(CompanyInvitation).where(CompanyInvitation.token == token)
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Приглашение не найдено.")
    if inv.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=410, detail="Приглашение уже использовано или отозвано.")
    if inv.expires_at < datetime.now(tz=timezone.utc):
        raise HTTPException(status_code=410, detail="Срок действия приглашения истёк.")
    # Название компании
    company_result = await db.execute(
        select(Company).where(Company.id == inv.company_id)
    )
    company = company_result.scalar_one_or_none()
    return {
        "email":        inv.email,
        "role":         inv.role.value,
        "company_name": company.name if company else "Компания",
        "company_id":   str(inv.company_id),
        "expires_at":   inv.expires_at.isoformat(),
    }


@router.get(
    "/me",
    summary="Текущий пользователь",
    description="Возвращает email, роль и company_id из токена. Используется для диагностики.",
)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {
        "user_id":    str(current_user.user_id),
        "company_id": str(current_user.company_id),
        "email":      current_user.email,
        "role":       current_user.role.value,
    }



@router.post(
    "/logout",
    summary="Выход из системы",
    description="Отзывает текущий токен — после этого он не будет принят даже если не истёк.",
)
async def logout(
    current_user: CurrentUser = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
) -> dict:
    """Инвалидирует JWT-токен пользователя (добавляет в blacklist)."""
    from app.services.jwt_service import decode_token
    try:
        if credentials:
            payload = decode_token(credentials.credentials)
            if payload.jti:
                revoke_token(payload.jti, payload.exp)
    except Exception:
        pass
    return {"message": "Выход выполнен успешно."}
