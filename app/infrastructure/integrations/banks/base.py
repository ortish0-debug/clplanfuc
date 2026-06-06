"""
Стратегия банковской интеграции (Strategy Pattern).
Каждый банк реализует BaseBankIntegration — ядро не меняется.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class BankTransaction:
    """Нормализованное представление банковской операции."""
    external_id: str
    amount: Decimal                  # Всегда Decimal, никогда float
    currency: str
    payment_date: date
    description: str
    counterparty: Optional[str] = None
    raw_data: Optional[dict] = None  # Оригинальный JSON от банка


@dataclass(frozen=True)
class BankBalance:
    account_external_id: str
    balance: Decimal
    currency: str
    as_of: date


class BaseBankIntegration(ABC):
    """
    Абстрактная стратегия для любой банковской интеграции.

    Реализации: SberbankIntegration, TinkoffIntegration, AlphaIntegration и т.д.
    Регистрируются в реестре интеграций без изменения ядра приложения.
    """

    # Человекочитаемый slug банка, например "sberbank", "tinkoff"
    bank_slug: str

    @abstractmethod
    async def authenticate(self, credentials: dict) -> bool:
        """Проверяет и сохраняет токены доступа."""
        ...

    @abstractmethod
    async def fetch_transactions(
        self,
        account_external_id: str,
        date_from: date,
        date_to: date,
    ) -> list[BankTransaction]:
        """Возвращает список операций за период."""
        ...

    @abstractmethod
    async def fetch_balance(
        self, account_external_id: str
    ) -> BankBalance:
        """Возвращает текущий баланс счёта."""
        ...

    @abstractmethod
    async def get_accounts(self) -> list[dict]:
        """Возвращает список доступных счетов клиента в банке."""
        ...
