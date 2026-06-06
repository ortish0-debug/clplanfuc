"""
FastAPI роутер: DirectBank — прямая банковская синхронизация (Спринт 8).

Эндпоинты:
  GET  /companies/{id}/bank-connections              — список подключений
  POST /companies/{id}/bank-connections              — подключить банк к счёту
  DELETE /companies/{id}/bank-connections/{conn_id} — отключить
  POST /companies/{id}/bank-connections/{conn_id}/sync — синхронизировать

Sync-эндпоинт — эмуляция реального банковского API:
  Генерирует набор «банковских» транзакций для указанного диапазона дат
  и сохраняет их в базу. В продакшене здесь будет вызов реального
  банковского OpenAPI (Тинькофф, Точка, СберБизнес и т.д.).

Идемпотентность синхронизации:
  Каждая «банковская» транзакция имеет уникальный bank_transaction_id.
  При повторной синхронизации дубликаты молча пропускаются
  (уникальный индекс uq_transactions_account_bank_id).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domain.models.integrations import BankConnection, BankConnectionStatus
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.saas_limit_service import check_bank_sync_allowed

router = APIRouter(tags=["DirectBank — банковская синхронизация"])


# ─────────────────────────────────────────────────────────────────────────────
# СХЕМЫ
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_BANKS = frozenset({"tinkoff", "tochka", "sber", "alfa", "custom"})


class BankConnectionCreate(BaseModel):
    account_id:     UUID
    bank_name:      str  = Field(..., description="tinkoff | tochka | sber | alfa | custom")
    access_token:   str  = Field(..., min_length=1, description="OAuth2 access token банка")
    refresh_token:  Optional[str] = None
    sync_from_date: Optional[date] = Field(
        None,
        description="С какой даты начать первую синхронизацию (по умолчанию — 30 дней назад)",
    )
    auto_classify:  bool = Field(True, description="Применять AI-классификатор к импортированным транзакциям")


class BankConnectionResponse(BaseModel):
    id:             UUID
    company_id:     UUID
    account_id:     UUID
    bank_name:      str
    status:         str
    last_sync_at:   Optional[datetime]
    last_error:     Optional[str]
    sync_from_date: Optional[date]
    auto_classify:  bool
    is_token_expired: bool
    created_at:     datetime
    updated_at:     datetime


class SyncResultResponse(BaseModel):
    connection_id:       UUID
    bank_name:           str
    transactions_synced: int
    transactions_skipped_duplicates: int
    date_range_start:    date
    date_range_end:      date
    status:              str


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_connection_or_404(
    db:            AsyncSession,
    connection_id: UUID,
    company_id:    UUID,
) -> BankConnection:
    result = await db.execute(
        select(BankConnection).where(
            and_(
                BankConnection.id         == connection_id,
                BankConnection.company_id == company_id,
            )
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Подключение {connection_id} не найдено.",
        )
    return conn


def _to_response(conn: BankConnection) -> BankConnectionResponse:
    return BankConnectionResponse(
        id=conn.id,
        company_id=conn.company_id,
        account_id=conn.account_id,
        bank_name=conn.bank_name,
        status=conn.status.value,
        last_sync_at=conn.last_sync_at,
        last_error=conn.last_error,
        sync_from_date=conn.sync_from_date,
        auto_classify=conn.auto_classify,
        is_token_expired=conn.is_token_expired,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


def _fake_bank_id(connection_id: UUID, txn_date: date, index: int) -> str:
    """Детерминированный bank_transaction_id для идемпотентности синхронизации."""
    raw = f"fake:{connection_id}:{txn_date.isoformat()}:{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/bank-connections",
    response_model=list[BankConnectionResponse],
    summary="Список банковских подключений",
)
async def list_connections(
    company_id:   UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> list[BankConnectionResponse]:
    result = await db.execute(
        select(BankConnection)
        .where(BankConnection.company_id == company_id)
        .order_by(BankConnection.created_at.desc())
    )
    return [_to_response(c) for c in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/bank-connections",
    response_model=BankConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Подключить банк к расчётному счёту",
    description=(
        "Создаёт карточку банковского подключения. "
        "Перед созданием проверяет тарифные ограничения: "
        "флаг `can_sync_banks` и лимит `max_bank_connections`. "
        "Если лимит превышен — возвращает HTTP 402."
    ),
)
async def create_connection(
    company_id:   UUID,
    body:         BankConnectionCreate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> BankConnectionResponse:
    if body.bank_name not in _ALLOWED_BANKS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестный банк: {body.bank_name!r}. "
                   f"Допустимые значения: {sorted(_ALLOWED_BANKS)}",
        )

    # ── Тарифная проверка (бросает HTTP 402 при превышении) ──────────────
    await check_bank_sync_allowed(db=db, company_id=company_id)

    # ── Проверяем что счёт принадлежит компании ──────────────────────────
    account_result = await db.execute(
        select(Account).where(
            and_(
                Account.id         == body.account_id,
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
            )
        )
    )
    if not account_result.scalar_one_or_none():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Счёт {body.account_id} не найден в компании.",
        )

    sync_start = body.sync_from_date or (date.today() - timedelta(days=30))

    conn = BankConnection(
        id=uuid.uuid4(),
        company_id=company_id,
        account_id=body.account_id,
        bank_name=body.bank_name,
        access_token=body.access_token,
        refresh_token=body.refresh_token,
        status=BankConnectionStatus.ACTIVE,
        sync_from_date=sync_start,
        auto_classify=body.auto_classify,
        meta={"source": "api", "created_by": str(current_user.user_id)},
    )
    db.add(conn)
    await db.flush()
    return _to_response(conn)


# ─────────────────────────────────────────────────────────────────────────────
# DISCONNECT
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/bank-connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отключить банк",
    description="Переводит подключение в статус DISCONNECTED. Ранее синхронизированные транзакции сохраняются.",
)
async def disconnect(
    company_id:    UUID,
    connection_id: UUID,
    current_user:  CurrentUser  = Depends(CanWriteFinance),
    db:            AsyncSession = Depends(get_db),
) -> None:
    conn = await _get_connection_or_404(db, connection_id, company_id)
    conn.status = BankConnectionStatus.DISCONNECTED
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# SYNC (эмуляция банковского API)
# ─────────────────────────────────────────────────────────────────────────────

# Шаблоны «банковских» транзакций для эмуляции
_FAKE_BANK_TEMPLATES = [
    (TransactionType.INCOME,  Decimal("150000.00"), "Поступление от клиента"),
    (TransactionType.EXPENSE, Decimal("45000.00"),  "Оплата поставщику"),
    (TransactionType.INCOME,  Decimal("89500.00"),  "Перевод от контрагента"),
    (TransactionType.EXPENSE, Decimal("12300.00"),  "Комиссия банка"),
    (TransactionType.EXPENSE, Decimal("67800.00"),  "Оплата аренды"),
    (TransactionType.INCOME,  Decimal("230000.00"), "Поступление выручки"),
    (TransactionType.EXPENSE, Decimal("38100.00"),  "Зарплата сотрудникам"),
]


@router.post(
    "/companies/{company_id}/bank-connections/{connection_id}/sync",
    response_model=SyncResultResponse,
    summary="Синхронизировать транзакции с банком",
    description=(
        "Эмулирует получение транзакций из банковского API за указанный период "
        "и сохраняет их в базу. "
        "В продакшене заменяется на реальный вызов OpenAPI банка. "
        "Идемпотентен: повторный вызов за тот же период не создаёт дубликатов "
        "(уникальность по bank_transaction_id)."
    ),
)
async def sync_transactions(
    company_id:    UUID,
    connection_id: UUID,
    sync_from:     Optional[date] = None,
    sync_to:       Optional[date] = None,
    current_user:  CurrentUser  = Depends(CanWriteFinance),
    db:            AsyncSession = Depends(get_db),
) -> SyncResultResponse:
    conn = await _get_connection_or_404(db, connection_id, company_id)

    if conn.status == BankConnectionStatus.DISCONNECTED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Подключение отключено. Переподключите банк перед синхронизацией.",
        )

    # Загружаем привязанный счёт
    account_result = await db.execute(
        select(Account).where(Account.id == conn.account_id)
    )
    account: Optional[Account] = account_result.scalar_one_or_none()
    if not account:
        conn.mark_error("Привязанный счёт не найден.")
        await db.flush()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Привязанный счёт удалён. Обновите подключение.",
        )

    # Определяем диапазон дат
    date_from = sync_from or conn.sync_from_date or (date.today() - timedelta(days=30))
    date_to   = sync_to   or date.today()

    if date_from > date_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sync_from не может быть позже sync_to.",
        )

    # ── Генерируем «банковские» транзакции (эмуляция) ────────────────────
    synced = 0
    skipped_duplicates = 0
    total_days = (date_to - date_from).days + 1
    templates  = _FAKE_BANK_TEMPLATES

    try:
        for day_offset in range(min(total_days, len(templates))):
            txn_date  = date_from + timedelta(days=day_offset)
            template  = templates[day_offset % len(templates)]
            txn_type, amount, description = template

            bank_txn_id = _fake_bank_id(conn.id, txn_date, day_offset)

            # INSERT ... ON CONFLICT DO NOTHING — идемпотентный импорт
            stmt = (
                pg_insert(Transaction)
                .values(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    account_id=conn.account_id,
                    transaction_type=txn_type.value,
                    status=TransactionStatus.CONFIRMED.value,
                    amount=amount,
                    currency=account.currency.value,
                    exchange_rate=Decimal("1.000000"),
                    amount_base_currency=amount,
                    payment_date=txn_date,
                    accrual_date=txn_date,
                    description=f"[{conn.bank_name.upper()}] {description}",
                    bank_transaction_id=bank_txn_id,
                    tags=[],
                    meta={"source": "directbank", "bank": conn.bank_name},
                    ai_classified=False,
                    is_deleted=False,
                )
                .on_conflict_do_nothing(
                    constraint="uq_transactions_account_bank_id"
                )
            )
            result = await db.execute(stmt)
            if result.rowcount:
                synced += 1
            else:
                skipped_duplicates += 1

        conn.mark_synced()

    except Exception as exc:
        conn.mark_error(str(exc)[:512])
        await db.flush()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Ошибка синхронизации с банком: {exc}",
        ) from exc

    await db.flush()

    return SyncResultResponse(
        connection_id=conn.id,
        bank_name=conn.bank_name,
        transactions_synced=synced,
        transactions_skipped_duplicates=skipped_duplicates,
        date_range_start=date_from,
        date_range_end=date_to,
        status=conn.status.value,
    )
