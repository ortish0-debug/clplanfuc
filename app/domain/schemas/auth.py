"""
Pydantic v2 схемы для системы авторизации (JWT).
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

TaxRegimeType = Literal[
    "osn",        # ОСНО
    "usn_income", # УСН «Доходы» 6%
    "usn_profit", # УСН «Доходы минус расходы» 15%
    "patent",     # Патент (ПСН)
    "eshn",       # ЕСХН
    "npd",        # НПД (самозанятые)
]


class RegisterRequest(BaseModel):
    """Запрос на регистрацию нового пользователя."""
    email:        EmailStr       = Field(..., description="Email адрес")
    password:     str            = Field(..., min_length=8, max_length=128, description="Пароль (минимум 8 символов)")
    full_name:    str            = Field(..., min_length=1, max_length=255, description="Полное имя")
    company_name: Optional[str]  = Field(None, max_length=255, description="Название компании (опционально)")
    tax_regime:   TaxRegimeType  = Field("usn_income", description="Налоговый режим компании")


class TokenRequest(BaseModel):
    """Запрос на получение JWT-токена."""
    email:      EmailStr = Field(..., description="Email адрес")
    password:   str      = Field(..., description="Пароль")
    code:       Optional[str] = Field(None, description="2FA TOTP код (если включена двухфакторная защита)")


class TokenResponse(BaseModel):
    """Ответ с JWT-токеном."""
    access_token:  str       = Field(..., description="JWT-токен для доступа (Bearer token)")
    token_type:    str       = Field(default="bearer", description="Тип токена")
    user_id:       UUID      = Field(..., description="UUID пользователя")
    company_id:    UUID      = Field(..., description="UUID компании")
    email:         str       = Field(..., description="Email пользователя")
    full_name:     str       = Field(..., description="Полное имя пользователя")
    role:          str       = Field(..., description="Роль пользователя в компании (owner/admin/accountant/viewer/manager)")
    expires_in:    int       = Field(..., description="Время жизни токена в секундах")


class RegisterResponse(BaseModel):
    """Ответ на успешную регистрацию."""
    model_config = ConfigDict(from_attributes=True)

    user_id:       UUID           = Field(..., description="UUID создано пользователя")
    company_id:    UUID           = Field(..., description="UUID созданной компании")
    email:         str            = Field(..., description="Email пользователя")
    full_name:     str            = Field(..., description="Полное имя")
    tax_regime:    TaxRegimeType  = Field(..., description="Выбранный налоговый режим")
    message:       str            = Field(default="Пользователь и компания успешно созданы", description="Статус")


class TaxRegimeInfo(BaseModel):
    """Информация о налоговом режиме."""
    code:        str = Field(..., description="Код режима")
    name:        str = Field(..., description="Название")
    description: str = Field(..., description="Краткое описание")
    rate:        str = Field(..., description="Ставка налога")


class UpdateTaxRegimeRequest(BaseModel):
    """Запрос на смену налогового режима."""
    tax_regime: TaxRegimeType = Field(..., description="Новый налоговый режим")
