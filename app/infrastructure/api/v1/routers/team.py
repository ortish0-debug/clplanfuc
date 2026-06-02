"""
FastAPI роутер: управление командой компании (Спринт 5).

Эндпоинты:
  POST   /companies/{id}/team/invite              — создать приглашение (OWNER/ADMIN)
  GET    /companies/{id}/team/members             — список участников
  PATCH  /companies/{id}/team/members/{user_id}   — изменить роль
  DELETE /companies/{id}/team/members/{user_id}   — удалить из команды

  POST   /auth/accept-invite                      — принять приглашение по токену
  GET    /companies/{id}/team/invitations         — список активных приглашений
  DELETE /companies/{id}/team/invitations/{inv_id}— отозвать приглашение
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domain.schemas.auth import TokenResponse
from app.domain.schemas.team import (
    AcceptInviteRequest,
    InviteRequest,
    InviteResponse,
    PendingInvitationResponse,
    TeamMemberResponse,
    TeamMemberRoleUpdate,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanManageTeam,
    CanViewDashboard,
    CurrentUser,
    get_current_user,
)
from app.infrastructure.database.session import get_db
from app.services.jwt_service import encode_token, TOKEN_EXPIRE_MINUTES

_INVITE_EXPIRE_DAYS = 7

router = APIRouter(tags=["Команда"])


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _role_from_str(role_str: str) -> UserRole:
    """Конвертирует строку роли в UserRole, запрещая назначение OWNER через API."""
    mapping = {
        "admin":      UserRole.ADMIN,
        "accountant": UserRole.ACCOUNTANT,
        "viewer":     UserRole.VIEWER,
        "manager":    UserRole.MANAGER,
    }
    if role_str not in mapping:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Недопустимая роль: {role_str!r}. Допустимые: admin, accountant, viewer, manager.",
        )
    return mapping[role_str]


async def _get_or_create_free_plan(db: AsyncSession) -> SubscriptionPlan:
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.slug == "free"))
    plan = result.scalar_one_or_none()
    if plan:
        return plan
    plan = SubscriptionPlan(
        id=uuid.uuid4(), slug="free", name="Бесплатный",
        price_monthly_cents=0, price_quarterly_cents=0, price_annual_cents=0,
        currency_code="RUB",
        feature_flags={"can_use_ai": False, "max_accounts": 3, "max_users": 5},
        is_public=True, is_active=True, sort_order=0,
    )
    db.add(plan)
    await db.flush()
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# СПИСОК УЧАСТНИКОВ
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/team/members",
    response_model=list[TeamMemberResponse],
    summary="Список участников команды",
)
async def list_members(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list[TeamMemberResponse]:
    result = await db.execute(
        select(UserCompanyRole, User.email, User.full_name)
        .join(User, UserCompanyRole.user_id == User.id)
        .where(
            and_(
                UserCompanyRole.company_id == company_id,
                UserCompanyRole.is_active.is_(True),
            )
        )
        .order_by(UserCompanyRole.joined_at)
    )
    rows = result.all()
    return [
        TeamMemberResponse(
            user_id=row.UserCompanyRole.user_id,
            email=row.email,
            full_name=row.full_name,
            role=row.UserCompanyRole.role.value,
            is_active=row.UserCompanyRole.is_active,
            joined_at=row.UserCompanyRole.joined_at,
        )
        for row in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ИЗМЕНЕНИЕ РОЛИ УЧАСТНИКА
# ─────────────────────────────────────────────────────────────────────────────


@router.patch(
    "/companies/{company_id}/team/members/{target_user_id}",
    response_model=TeamMemberResponse,
    summary="Изменить роль участника",
)
async def update_member_role(
    company_id:     UUID,
    target_user_id: UUID,
    body:           TeamMemberRoleUpdate,
    current_user:   CurrentUser = Depends(CanManageTeam),
    db:             AsyncSession = Depends(get_db),
) -> TeamMemberResponse:
    if target_user_id == current_user.user_id:
        raise HTTPException(422, detail="Нельзя изменить собственную роль.")

    result = await db.execute(
        select(UserCompanyRole, User.email, User.full_name)
        .join(User, UserCompanyRole.user_id == User.id)
        .where(
            and_(
                UserCompanyRole.user_id == target_user_id,
                UserCompanyRole.company_id == company_id,
                UserCompanyRole.is_active.is_(True),
            )
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(404, detail="Участник не найден.")

    target_role_record = row.UserCompanyRole
    # OWNER не может быть переназначен через API
    if target_role_record.role == UserRole.OWNER:
        raise HTTPException(422, detail="Роль OWNER нельзя изменить через API.")

    new_role = _role_from_str(body.role)
    target_role_record.role = new_role
    await db.flush()

    return TeamMemberResponse(
        user_id=target_user_id,
        email=row.email,
        full_name=row.full_name,
        role=new_role.value,
        is_active=True,
        joined_at=target_role_record.joined_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# УДАЛЕНИЕ ИЗ КОМАНДЫ
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/team/members/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить участника из команды",
)
async def remove_member(
    company_id:     UUID,
    target_user_id: UUID,
    current_user:   CurrentUser = Depends(CanManageTeam),
    db:             AsyncSession = Depends(get_db),
) -> None:
    if target_user_id == current_user.user_id:
        raise HTTPException(422, detail="Нельзя удалить себя из команды.")

    result = await db.execute(
        select(UserCompanyRole).where(
            and_(
                UserCompanyRole.user_id == target_user_id,
                UserCompanyRole.company_id == company_id,
                UserCompanyRole.is_active.is_(True),
            )
        )
    )
    role_record = result.scalar_one_or_none()
    if not role_record:
        raise HTTPException(404, detail="Участник не найден.")
    if role_record.role == UserRole.OWNER:
        raise HTTPException(422, detail="Нельзя удалить владельца компании.")

    role_record.is_active = False
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# СОЗДАНИЕ ПРИГЛАШЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/team/invite",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Пригласить сотрудника",
    description=(
        "Создаёт токен-приглашение и возвращает ссылку для принятия. "
        "В production ссылка отправляется на email; здесь она возвращается в ответе для разработки."
    ),
)
async def create_invite(
    company_id:   UUID,
    body:         InviteRequest,
    current_user: CurrentUser = Depends(CanManageTeam),
    db:           AsyncSession = Depends(get_db),
) -> InviteResponse:
    # Проверяем, не состоит ли пользователь уже в команде
    existing_user = await db.execute(select(User).where(User.email == body.email))
    user_obj = existing_user.scalar_one_or_none()
    if user_obj:
        existing_role = await db.execute(
            select(UserCompanyRole).where(
                and_(
                    UserCompanyRole.user_id == user_obj.id,
                    UserCompanyRole.company_id == company_id,
                    UserCompanyRole.is_active.is_(True),
                )
            )
        )
        if existing_role.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Пользователь {body.email} уже является участником команды.",
            )

    # Аннулируем предыдущие активные приглашения на этот email
    old_invites = await db.execute(
        select(CompanyInvitation).where(
            and_(
                CompanyInvitation.company_id == company_id,
                CompanyInvitation.email == body.email,
                CompanyInvitation.status == InvitationStatus.PENDING,
            )
        )
    )
    for old_inv in old_invites.scalars().all():
        old_inv.status = InvitationStatus.REVOKED

    # Создаём новое приглашение
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=_INVITE_EXPIRE_DAYS)
    role = _role_from_str(body.role)

    invitation = CompanyInvitation(
        id=uuid.uuid4(),
        company_id=company_id,
        invited_by_user_id=current_user.user_id,
        email=body.email,
        role=role,
        token=token,
        status=InvitationStatus.PENDING,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.flush()

    return InviteResponse(
        invitation_id=invitation.id,
        email=body.email,
        role=role.value,
        token=token,
        expires_at=expires_at,
        invite_url=f"/auth/accept-invite?token={token}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# СПИСОК АКТИВНЫХ ПРИГЛАШЕНИЙ
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/team/invitations",
    response_model=list[PendingInvitationResponse],
    summary="Список активных приглашений",
)
async def list_invitations(
    company_id:   UUID,
    current_user: CurrentUser = Depends(CanManageTeam),
    db:           AsyncSession = Depends(get_db),
) -> list[PendingInvitationResponse]:
    result = await db.execute(
        select(CompanyInvitation).where(
            and_(
                CompanyInvitation.company_id == company_id,
                CompanyInvitation.status == InvitationStatus.PENDING,
            )
        ).order_by(CompanyInvitation.created_at.desc())
    )
    return [
        PendingInvitationResponse(
            id=inv.id,
            email=inv.email,
            role=inv.role.value,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
            status=inv.status.value,
        )
        for inv in result.scalars().all()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ОТЗЫВ ПРИГЛАШЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/team/invitations/{inv_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать приглашение",
)
async def revoke_invitation(
    company_id:   UUID,
    inv_id:       UUID,
    current_user: CurrentUser = Depends(CanManageTeam),
    db:           AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(CompanyInvitation).where(
            and_(
                CompanyInvitation.id == inv_id,
                CompanyInvitation.company_id == company_id,
            )
        )
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Приглашение не найдено.")
    inv.status = InvitationStatus.REVOKED
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# ПРИНЯТИЕ ПРИГЛАШЕНИЯ (публичный эндпоинт без аутентификации)
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/auth/accept-invite",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Принять приглашение в команду",
    description=(
        "Принимает токен приглашения. "
        "Если пользователь с этим email уже существует — достаточно передать только token. "
        "Если пользователь новый — нужно передать password и full_name."
    ),
)
async def accept_invite(
    body: AcceptInviteRequest,
    db:   AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Ищем приглашение
    result = await db.execute(
        select(CompanyInvitation).where(CompanyInvitation.token == body.token)
    )
    inv: Optional[CompanyInvitation] = result.scalar_one_or_none()

    if not inv:
        raise HTTPException(404, "Приглашение не найдено.")
    if inv.status != InvitationStatus.PENDING:
        raise HTTPException(
            status.HTTP_410_GONE,
            f"Приглашение уже использовано или отозвано (статус: {inv.status.value}).",
        )
    if inv.expires_at < datetime.now(tz=timezone.utc):
        inv.status = InvitationStatus.EXPIRED
        await db.flush()
        raise HTTPException(status.HTTP_410_GONE, "Срок действия приглашения истёк.")

    # Ищем или создаём пользователя
    user_result = await db.execute(select(User).where(User.email == inv.email))
    user: Optional[User] = user_result.scalar_one_or_none()

    if user is None:
        # Новый пользователь — нужен пароль
        if not body.password:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Для нового пользователя необходимо передать `password`.",
            )
        user = User(
            id=uuid.uuid4(),
            email=inv.email,
            hashed_password=_hash_password(body.password),
            full_name=body.full_name or inv.email.split("@")[0],
            is_active=True,
            is_verified=True,   # Инвайт = верификация email
            is_superuser=False,
            locale="ru",
            timezone="Europe/Moscow",
        )
        db.add(user)
        await db.flush()

    # Добавляем роль в компании
    existing_role = await db.execute(
        select(UserCompanyRole).where(
            and_(
                UserCompanyRole.user_id == user.id,
                UserCompanyRole.company_id == inv.company_id,
            )
        )
    )
    role_record = existing_role.scalar_one_or_none()
    if role_record:
        # Пользователь уже в компании — обновляем роль
        role_record.is_active = True
        role_record.role = inv.role
    else:
        role_record = UserCompanyRole(
            id=uuid.uuid4(),
            user_id=user.id,
            company_id=inv.company_id,
            role=inv.role,
            is_active=True,
            custom_permissions={},
            invited_at=inv.created_at,
            joined_at=datetime.now(tz=timezone.utc),
        )
        db.add(role_record)

    # Инвалидируем приглашение
    inv.status = InvitationStatus.ACCEPTED
    inv.accepted_at = datetime.now(tz=timezone.utc)
    await db.flush()

    # Возвращаем JWT для немедленного входа
    expires_in = TOKEN_EXPIRE_MINUTES * 60
    token = encode_token(
        user_id=user.id,
        company_id=inv.company_id,
        email=user.email,
        expires_in=expires_in,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        company_id=inv.company_id,
        email=user.email,
        full_name=user.full_name or "",
        role=inv.role.value,
        expires_in=expires_in,
    )
