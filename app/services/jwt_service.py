"""
Сервис JWT: кодирование и декодирование токенов доступа.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import jwt
from jwt import DecodeError, ExpiredSignatureError, InvalidTokenError

# Из .env
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 часа


class JWTPayload:
    """Структура данных токена."""

    def __init__(self, user_id: UUID, company_id: UUID, email: str):
        self.user_id    = user_id
        self.company_id = company_id
        self.email      = email


class TokenError(Exception):
    """Исключение при работе с токеном."""
    pass


def encode_token(user_id: UUID, company_id: UUID, email: str, expires_in: Optional[int] = None) -> str:
    """
    Кодирует JWT-токен с данными пользователя.

    Args:
        user_id: UUID пользователя
        company_id: UUID компании
        email: Email пользователя
        expires_in: Время жизни токена в секундах (по умолчанию TOKEN_EXPIRE_MINUTES * 60)

    Returns:
        Закодированная JWT-строка
    """
    if expires_in is None:
        expires_in = TOKEN_EXPIRE_MINUTES * 60

    now = datetime.now(tz=timezone.utc)
    payload = {
        "user_id":    str(user_id),
        "company_id": str(company_id),
        "email":      email,
        "iat":        int(now.timestamp()),
        "exp":        int((now + timedelta(seconds=expires_in)).timestamp()),
    }

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


def decode_token(token: str) -> JWTPayload:
    """
    Декодирует JWT-токен и проверяет подпись + срок действия.

    Args:
        token: JWT-строка

    Returns:
        JWTPayload с user_id, company_id, email

    Raises:
        TokenError: Если токен невалиден, истёк или подпись неверна
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise TokenError("Токен истёк. Пожалуйста, переавторизируйтесь.")
    except (DecodeError, InvalidTokenError) as exc:
        raise TokenError(f"Невалидный токен: {exc}")

    try:
        user_id = UUID(payload["user_id"])
        company_id = UUID(payload["company_id"])
        email = payload["email"]
    except (KeyError, ValueError) as exc:
        raise TokenError(f"Некорректные данные в токене: {exc}")

    return JWTPayload(user_id=user_id, company_id=company_id, email=email)


def extract_token_from_header(auth_header: str) -> str:
    """
    Извлекает JWT из заголовка Authorization: Bearer <token>.

    Args:
        auth_header: Значение заголовка Authorization

    Returns:
        JWT-строка

    Raises:
        TokenError: Если формат неверный
    """
    if not auth_header:
        raise TokenError("Заголовок Authorization отсутствует")

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenError("Неверный формат Authorization: используйте 'Bearer <token>'")

    return parts[1]
