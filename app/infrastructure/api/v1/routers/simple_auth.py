"""Простой endpoint входа для SQLite базы данных (без ORM)."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import jwt
import bcrypt
import sqlite3
from uuid import uuid4

router = APIRouter(prefix="/auth", tags=["Simple Auth"])

SECRET_KEY = "dev_secret_key_32_chars_minimum_x"
ALGORITHM = "HS256"


class TokenRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    company_id: str
    email: str
    full_name: str


@router.post("/token", response_model=TokenResponse)
async def login(body: TokenRequest) -> TokenResponse:
    """Простой вход через SQLite (без ORM)."""
    conn = sqlite3.connect("planfact.db")
    cursor = conn.cursor()

    # Получаю пользователя по email
    cursor.execute("SELECT id, password, full_name FROM users WHERE email = ?", (body.email,))
    user = cursor.fetchone()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные учётные данные",
        )

    user_id, hashed_password, full_name = user

    # Проверяю пароль
    if not bcrypt.checkpw(body.password.encode("utf-8"), hashed_password.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные учётные данные",
        )

    # Получаю компанию пользователя (или создаю)
    cursor.execute("SELECT id FROM companies LIMIT 1")
    company = cursor.fetchone()

    if not company:
        # Создаю компанию если её нет
        company_id = str(uuid4())
        cursor.execute(
            "INSERT INTO companies (id, name, created_at) VALUES (?, ?, ?)",
            (company_id, "Test Company", datetime.now().isoformat()),
        )
        conn.commit()
    else:
        company_id = company[0]

    conn.close()

    # Создаю JWT token
    payload = {
        "user_id": user_id,
        "email": body.email,
        "company_id": company_id,
        "exp": datetime.utcnow() + timedelta(days=30),
    }
    access_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=user_id,
        company_id=company_id,
        email=body.email,
        full_name=full_name,
    )
