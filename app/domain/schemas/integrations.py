"""
Pydantic v2 схемы: Банковские интеграции и синхронизация (Спринт 12).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# ЛОГ СИНХРОНИЗАЦИИ
# ─────────────────────────────────────────────────────────────────────────────


class BankSyncLogResponse(BaseModel):
    """
    Одна запись истории синхронизации с банком.

    status:
      'processing' — синхронизация в процессе (или зависла)
      'success'    — импорт завершён успешно
      'failed'     — ошибка; подробности в error_message
    """
    id:                 UUID
    company_id:         UUID
    bank_connection_id: UUID
    status:             str
    transactions_fetched: int
    error_message:      Optional[str]
    created_at:         datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# ОТВЕТ НА ЗАПУСК СИНХРОНИЗАЦИИ
# ─────────────────────────────────────────────────────────────────────────────


class BankSyncTriggerResponse(BaseModel):
    """
    Итог принудительного запуска банковской синхронизации.

    sync_log_id:          UUID созданной записи BankSyncLog (для polling статуса)
    status:               'success' | 'failed'
    transactions_fetched: число импортированных транзакций
    message:              человекочитаемый итог
    """
    sync_log_id:          UUID
    status:               str
    transactions_fetched: int
    message:              str
