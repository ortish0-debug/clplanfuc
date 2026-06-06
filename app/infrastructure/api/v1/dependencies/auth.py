"""
FastAPI Dependency Injection: аутентификация (JWT) и расширенный RBAC.

Матрица прав:
  OWNER      → всё, включая биллинг и управление командой
  ADMIN      → всё, кроме биллинга
  ACCOUNTANT → финансовые операции, импорт, все отчёты
  VIEWER     → чтение всех отчётов, без операций и команды
  MANAGER    → только дашборд + ДДС + P&L; без Баланса/Долгов/Команды

CurrentUser теперь несёт поле `role` — загружается из DB один раз
в get_current_user и переиспользуется во всех зависимостях.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.saas import User, UserCompanyRole, UserRole
from app.infrastructure.database.session import get_db
from app.services.jwt_service import decode_token, TokenError

_bearer_scheme = HTTPBearer(auto_error=True)


# ─────────────────────────────────────────────────────────────────────────────
# КОНТЕКСТ ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CurrentUser:
    user_id:      UUID
    company_id:   UUID
    email:        str
    role:         UserRole   # Роль в компании — загружается из DB один раз
    is_superuser: bool


# ─────────────────────────────────────────────────────────────────────────────
# JWT ДЕКОДИРОВАНИЕ + ЗАГРУЗКА РОЛИ
# ─────────────────────────────────────────────────────────────────────────────


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    1. Декодирует JWT и проверяет подпись + срок действия.
    2. Загружает User из БД (active check).
    3. Загружает роль из user_company_roles.
    4. Возвращает CurrentUser с role — все downstream-зависимости
       получают роль без дополнительных DB-запросов.
    """
    token = credentials.credentials

    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Проверяем что пользователь существует и активен
    user_result = await db.execute(
        select(User).where(
            and_(User.id == payload.user_id, User.is_active.is_(True))
        )
    )
    user: Optional[User] = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден или деактивирован.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Суперпользователям роль не нужна
    if user.is_superuser:
        return CurrentUser(
            user_id=payload.user_id,
            company_id=payload.company_id,
            email=payload.email,
            role=UserRole.OWNER,
            is_superuser=True,
        )

    # Загружаем роль в компании
    role_result = await db.execute(
        select(UserCompanyRole.role).where(
            and_(
                UserCompanyRole.user_id == payload.user_id,
                UserCompanyRole.company_id == payload.company_id,
                UserCompanyRole.is_active.is_(True),
            )
        )
    )
    role: Optional[UserRole] = role_result.scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="У вас нет активной роли в данной компании.",
        )

    return CurrentUser(
        user_id=payload.user_id,
        company_id=payload.company_id,
        email=payload.email,
        role=role,
        is_superuser=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ФАБРИКА RBAC-ЗАВИСИМОСТЕЙ
# ─────────────────────────────────────────────────────────────────────────────


def require_role(*allowed_roles: UserRole):
    """
    Фабрика зависимостей для проверки роли.

    Использование:
        async def my_view(user = Depends(require_role(UserRole.OWNER, UserRole.ADMIN))):
            ...

    Дополнительно проверяет IDOR: company_id из URL-пути должен совпадать
    с company_id в JWT-токене пользователя.
    """

    async def _check(
        company_id: UUID,                                    # из URL-пути
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.is_superuser:
            return current_user

        # IDOR-защита: пользователь не может обращаться к чужой компании
        if company_id != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Доступ к данной компании запрещён.",
            )

        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Недостаточно прав. "
                    f"Требуется одна из ролей: {', '.join(r.value for r in allowed_roles)}. "
                    f"Ваша роль: {current_user.role.value}."
                ),
            )

        return current_user

    return _check


# ─────────────────────────────────────────────────────────────────────────────
# ГОТОВЫЕ ЗАВИСИМОСТИ
# ─────────────────────────────────────────────────────────────────────────────

# Дашборд и базовые операционные метрики (все роли)
CanViewDashboard = require_role(
    UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT,
    UserRole.VIEWER, UserRole.MANAGER,
)

# Полные финансовые отчёты (ДДС, P&L) — без MANAGER/VIEWER только по записи
CanViewReports = require_role(
    UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.VIEWER,
)

# Запись финансовых операций, импорт (OWNER, ADMIN, ACCOUNTANT)
CanWriteFinance = require_role(
    UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT,
)

# Операции которые может делать MANAGER: CRM, проекты, контрагенты, заявки
CanWriteOperational = require_role(
    UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.MANAGER,
)

# Управленческий баланс + долги — только финансовые роли (не MANAGER)
CanViewBalance = require_role(
    UserRole.OWNER, UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.VIEWER,
)

# Управление командой: инвайты, смена ролей
CanManageTeam = require_role(UserRole.OWNER, UserRole.ADMIN)

# Биллинг и подписка
CanManageBilling = require_role(UserRole.OWNER)

# Псевдонимы для обратной совместимости с роутерами Спринтов 2-4
CanManageCompany = require_role(UserRole.OWNER, UserRole.ADMIN)
