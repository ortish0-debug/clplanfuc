"""
Async SQLAlchemy engine и session factory для Google Cloud SQL (PostgreSQL).
Строка подключения читается из переменной окружения DATABASE_URL.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.database.config import get_database_url

# SQLite в памяти для тестов не поддерживает пулинг
_database_url = get_database_url()
_engine_kwargs = {
    "echo": False,
}

if "sqlite" not in _database_url:
    # PostgreSQL параметры пулинга
    _engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    })
else:
    # SQLite параметры
    from sqlalchemy.pool import StaticPool
    _engine_kwargs.update({
        "poolclass": StaticPool,
    })

_engine = create_async_engine(_database_url, **_engine_kwargs)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Dependency: выдаёт сессию и гарантирует её закрытие."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
