"""
Конфигурация подключения к БД.
Читает DATABASE_URL из окружения; при запуске на Cloud Run — через Cloud SQL connector.
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # загружает .env из текущей директории
except ImportError:
    pass  # python-dotenv не установлен — используем переменные окружения


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")

    # Fallback на SQLite для локальной разработки
    if not url:
        return "sqlite+aiosqlite:///./planfact.db"

    # asyncpg требует схему postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return url
