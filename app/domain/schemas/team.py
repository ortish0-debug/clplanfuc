"""
Pydantic v2 схемы для управления командой компании.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class InviteRequest(BaseModel):
    """Запрос на отправку приглашения."""
    email:    EmailStr = Field(..., description="Email сотрудника")
    role:     str      = Field(
        ...,
        description="Роль: admin | accountant | viewer | manager",
        pattern="^(admin|accountant|viewer|manager)$",
    )


class InviteResponse(BaseModel):
    """Ответ на создание приглашения."""
    invitation_id: UUID
    email:         str
    role:          str
    token:         str       = Field(..., description="Токен приглашения (в реальном проекте отправляется по email)")
    expires_at:    datetime
    invite_url:    str       = Field(..., description="Ссылка для принятия приглашения")


class AcceptInviteRequest(BaseModel):
    """Запрос на принятие приглашения."""
    token:     str = Field(..., description="Токен приглашения из email-ссылки")
    password:  Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Пароль для нового пользователя (если аккаунта ещё нет)",
    )
    full_name: Optional[str] = Field(None, max_length=255, description="Имя (для нового пользователя)")


class TeamMemberResponse(BaseModel):
    """Участник команды компании."""
    model_config = ConfigDict(from_attributes=True)

    user_id:    UUID
    email:      str
    full_name:  Optional[str]
    role:       str
    is_active:  bool
    joined_at:  Optional[datetime]


class TeamMemberRoleUpdate(BaseModel):
    """Изменение роли участника."""
    role: str = Field(
        ...,
        description="Новая роль: admin | accountant | viewer | manager",
        pattern="^(admin|accountant|viewer|manager)$",
    )


class PendingInvitationResponse(BaseModel):
    """Приглашение, ожидающее принятия."""
    id:         UUID
    email:      str
    role:       str
    created_at: datetime
    expires_at: datetime
    status:     str
