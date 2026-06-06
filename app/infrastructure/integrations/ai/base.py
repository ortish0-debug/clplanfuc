"""
Стратегия AI-парсера (Strategy Pattern).
Каждая LLM-интеграция (Gemini, GPT-4o и т.д.) реализует BaseAIParser.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class ParsedTransaction:
    """Результат AI-классификации одной финансовой операции."""
    suggested_category_id: Optional[uuid.UUID]
    suggested_category_name: Optional[str]
    confidence: Decimal                  # 0.0000 – 1.0000, Decimal
    tags: list[str]
    reasoning: Optional[str] = None     # Объяснение решения (для дебага)


@dataclass
class BatchParseRequest:
    transaction_id: uuid.UUID
    description: str
    counterparty: Optional[str]
    amount: Decimal
    currency: str


class BaseAIParser(ABC):
    """
    Абстрактная стратегия AI-классификации транзакций.

    Реализации: GeminiParser, OpenAIParser и т.д.
    Выбор реализации — через конфигурацию, без изменения Use Cases.
    """

    # Идентификатор модели, например "gemini-2.5-flash"
    model_id: str

    @abstractmethod
    async def classify_transaction(
        self,
        request: BatchParseRequest,
        available_categories: list[dict],
    ) -> ParsedTransaction:
        """Классифицирует одну транзакцию."""
        ...

    @abstractmethod
    async def classify_batch(
        self,
        requests: list[BatchParseRequest],
        available_categories: list[dict],
    ) -> list[ParsedTransaction]:
        """
        Массовая классификация транзакций.
        Реализация должна использовать batch API для снижения затрат.
        """
        ...

    @abstractmethod
    async def extract_from_document(
        self,
        document_content: bytes,
        mime_type: str,
    ) -> list[BatchParseRequest]:
        """
        Извлекает транзакции из документа (PDF-выписка, CSV, фото чека).
        Возвращает нормализованный список для последующей классификации.
        """
        ...
