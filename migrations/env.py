"""
Alembic env.py — настроен для async SQLAlchemy + asyncpg.
DATABASE_URL читается из .env через python-dotenv.
Все модели импортируются здесь, чтобы регистрировать таблицы в Base.metadata.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Optional

import dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Загружаем .env до любых импортов, зависящих от окружения
# ---------------------------------------------------------------------------
dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# Стандартная конфигурация Alembic
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Подставляем DATABASE_URL из окружения в конфиг Alembic.
# Это переопределяет значение sqlalchemy.url из alembic.ini.
# ---------------------------------------------------------------------------
database_url: Optional[str] = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL не задан. "
        "Убедитесь, что файл .env существует и содержит DATABASE_URL."
    )
config.set_main_option("sqlalchemy.url", database_url)

# ---------------------------------------------------------------------------
# Импортируем модели — это регистрирует все таблицы в Base.metadata.
# ВАЖНО: импорт должен происходить ДО передачи target_metadata в context.
# ---------------------------------------------------------------------------
from app.infrastructure.database.base import Base  # noqa: E402

# Каждый import ниже регистрирует таблицы через декларативный Base.
# Порядок важен: finance зависит от saas (FK users → transactions).
import app.domain.models.saas           # noqa: F401, E402  — users, subscriptions, roles
import app.domain.models.finance        # noqa: F401, E402  — companies, accounts, categories, transactions
import app.domain.models.rules          # noqa: F401, E402  — auto_rules
import app.domain.models.counterparties # noqa: F401, E402  — counterparties, invoices
import app.domain.models.projects       # noqa: F401, E402  — projects, budgets, payment_requests
import app.domain.models.assets_loans   # noqa: F401, E402  — assets, loans, loan_payment_schedule
import app.domain.models.integrations   # noqa: F401, E402  — bank_connections (Sprint 8)
import app.domain.models.accruals       # noqa: F401, E402  — accrual_documents, transaction_accrual_links (Sprint 9)
import app.domain.models.payroll        # noqa: F401, E402  — employees, payroll_calculations (Sprint 10)
import app.domain.models.holdings       # noqa: F401, E402  — intra_group_links (Sprint 11)
import app.domain.models.inventory      # noqa: F401, E402  — stock_items, warehouses, stock_operations (Sprint 13)
import app.domain.models.ledger         # noqa: F401, E402  — accounts_chart, journal_entries, ledger_lines (Sprint 14)
import app.domain.models.taxes          # noqa: F401, E402  — vat_records (Sprint 17)
import app.domain.models.fixed_assets   # noqa: F401, E402  — fixed_assets (Sprint 18)
import app.domain.models.loans          # noqa: F401, E402  — loan_contracts, loan_payment_schedules (Sprint 19)
import app.domain.models.audit          # noqa: F401, E402  — audit_logs (Phase 1)
import app.domain.models.currency       # noqa: F401, E402  — currency_rates (Sprint 21)
import app.domain.models.bank_integration # noqa: F401, E402  — bank_integrations (Sprint 22)
import app.domain.models.billing        # noqa: F401, E402  — subscriptions (Sprint 23)
import app.domain.models.hr_advanced    # noqa: F401, E402  — employee_leaves (Sprint 28)
import app.domain.models.warehouse      # noqa: F401, E402  — warehouses, stock_levels (Sprint 29)
import app.domain.models.warehouse_movements # noqa: F401, E402  — stock_movements (Sprint 30)
import app.domain.models.production     # noqa: F401, E402  — BOM, ProductionOrder (Sprint 32)
import app.domain.models.documents      # noqa: F401, E402  — Document, Links (Sprint 39)
import app.domain.models.planning       # noqa: F401, E402  — PlannedTransaction (Sprint 43)
import app.domain.models.onec_mapping   # noqa: F401, E402  — OneCMapping (Sprint 45)
import app.domain.models.alert_rules    # noqa: F401, E402  — AlertRule (Sprint 47)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline-режим: генерация SQL без подключения к БД
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """
    Запускает миграции в offline-режиме (без живого подключения).
    Полезно для генерации SQL-скриптов для ручного применения.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online-режим: реальное подключение к PostgreSQL через asyncpg
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Создаёт async-движок и выполняет миграции через run_sync.
    NullPool — обязателен для миграций, чтобы не оставлять открытые соединения.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
