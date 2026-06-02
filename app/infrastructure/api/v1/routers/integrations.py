"""
FastAPI роутер: Банковские интеграции — синхронизация и история (Спринт 12).

Эндпоинты:
  POST /companies/{id}/bank-connections/{conn_id}/sync
       — принудительный запуск полного цикла импорта через bank_sync_service.
         Заменяет устаревший мок-эндпоинт из directbank.py (регистрируется первым).

  GET  /companies/{id}/bank-connections/{conn_id}/sync-history
       — история последних N сессий синхронизации (телеметрия).

Приоритет маршрутов:
  Этот роутер регистрируется в main.py ДО directbank.router.
  FastAPI использует "первое совпадение побеждает" для дублирующихся путей,
  поэтому POST .../sync теперь обрабатывается bank_sync_service, а не
  устаревшей mock-логикой из directbank.py.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.integrations import BankSyncLogResponse, BankSyncTriggerResponse
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db
from app.services.bank_sync_service import (
    execute_bank_synchronization,
    get_connection_sync_history,
)

router = APIRouter(tags=["Интеграции — банковская синхронизация"])


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER SYNC
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/bank-connections/{connection_id}/sync",
    response_model=BankSyncTriggerResponse,
    summary="Принудительная синхронизация с банком",
    description=(
        "Запускает полный цикл импорта выписки: "
        "стягивает транзакции из банковского API, "
        "выполняет дедупликацию по bank_transaction_id, "
        "применяет AutoRule-категоризацию к новым операциям, "
        "создаёт запись BankSyncLog с итогами. "
        "Возвращает sync_log_id для последующего получения статуса."
    ),
)
async def trigger_sync(
    company_id:    UUID,
    connection_id: UUID,
    current_user:  CurrentUser  = Depends(CanWriteFinance),
    db:            AsyncSession = Depends(get_db),
) -> BankSyncTriggerResponse:
    log = await execute_bank_synchronization(
        db=db,
        company_id=company_id,
        connection_id=connection_id,
    )
    if log.status == "success":
        msg = (
            f"Синхронизация завершена. "
            f"Импортировано: {log.transactions_fetched} транзакций."
        )
    else:
        msg = f"Ошибка синхронизации: {log.error_message or 'неизвестная ошибка'}"

    return BankSyncTriggerResponse(
        sync_log_id=log.id,
        status=log.status,
        transactions_fetched=log.transactions_fetched,
        message=msg,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SYNC HISTORY
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/bank-connections/{connection_id}/sync-history",
    response_model=list[BankSyncLogResponse],
    summary="История сессий банковской синхронизации",
    description=(
        "Возвращает последние N записей BankSyncLog для данного подключения. "
        "Используется для отображения телеметрии в разделе DirectBank: "
        "когда последний раз синхронизировались, сколько транзакций импортировано, "
        "были ли ошибки."
    ),
)
async def sync_history(
    company_id:    UUID,
    connection_id: UUID,
    limit:         int          = Query(10, ge=1, le=50, description="Число записей истории"),
    current_user:  CurrentUser  = Depends(CanViewDashboard),
    db:            AsyncSession = Depends(get_db),
) -> list[BankSyncLogResponse]:
    logs = await get_connection_sync_history(
        db=db,
        company_id=company_id,
        connection_id=connection_id,
        limit=limit,
    )
    return [BankSyncLogResponse.model_validate(log) for log in logs]
