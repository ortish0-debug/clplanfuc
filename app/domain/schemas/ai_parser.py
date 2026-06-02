"""
Pydantic v2 схемы для AI-парсера транзакций.

GeminiResponseStructure  — передаётся как response_schema в Gemini SDK,
                           гарантирует 100% валидный структурированный JSON.
AIParseResponseSchema    — ответ фронтенду после создания транзакции.
ParseRawTransactionRequest — тело POST-запроса.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

# ---------------------------------------------------------------------------
# Переиспользуем тип DecimalStr из финансовых схем
# ---------------------------------------------------------------------------

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Структура, которую Gemini SDK использует как JSON Schema (response_schema).
# Не содержит кастомных сериализаторов — только нативные типы Python.
# Gemini SDK сам конвертирует эту модель в JSON Schema при передаче в API.
# ---------------------------------------------------------------------------


class GeminiResponseStructure(BaseModel):
    """
    Строгая схема ответа Gemini.
    Все поля обязательны (кроме counterparty) — это заставляет модель
    всегда заполнять структуру, а не пропускать поля.

    amount хранится как str, а не float, чтобы избежать потери точности
    при передаче через JSON (например, 1500.00 → 1500.0 у float).
    """

    model_config = ConfigDict(
        # populate_by_name нужен для корректной работы со старыми alias
        populate_by_name=True,
    )

    # Сумма операции: "1500.00", "99999.99" — только цифры и точка
    amount: str = Field(
        ...,
        description="Сумма операции в виде строки, например '1500.00'. Только цифры и точка.",
        pattern=r"^\d+(\.\d{1,2})?$",
    )

    # Тип операции — строго три варианта
    transaction_type: Literal["income", "expense", "transfer"] = Field(
        ...,
        description=(
            "Тип операции: "
            "income — поступление денег, "
            "expense — расход/оплата, "
            "transfer — перевод между счетами."
        ),
    )

    # Контрагент (ООО, ИП, физлицо) — может отсутствовать
    counterparty: Optional[str] = Field(
        default=None,
        max_length=512,
        description="Название контрагента, если упоминается в тексте.",
    )

    # Краткое нормализованное описание (не более 100 символов)
    description: str = Field(
        ...,
        max_length=100,
        description="Краткое описание операции, не более 100 символов.",
    )

    # Slug ближайшей категории из предоставленного списка
    suggested_category_slug: str = Field(
        ...,
        max_length=64,
        description=(
            "Slug наиболее подходящей категории из предоставленного списка. "
            "Если ни одна не подходит — вернуть 'other'."
        ),
    )

    # Уверенность от 0.0 до 1.0
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Уверенность классификации от 0.0 (нет уверенности) до 1.0 (абсолютная).",
    )


# ---------------------------------------------------------------------------
# Схема запроса от фронтенда
# ---------------------------------------------------------------------------


class ParseRawTransactionRequest(BaseModel):
    """Тело POST /companies/{id}/transactions/parse-raw."""

    # Сырой текст: описание банковской выписки, голосовое сообщение, фото чека
    raw_text: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Произвольный текст операции для AI-классификации.",
        examples=[
            "Оплата ООО Ромашка за поставку канцтоваров 15000 руб 12.05.2025",
            "Получили от клиента Иванов И.И. предоплату 50 000 рублей за разработку",
        ],
    )

    # На какой счёт зачислять / с какого счёта списывать
    account_id: UUID = Field(
        ...,
        description="UUID счёта компании, к которому привязывается операция.",
    )

    # Если не передать — подставится сегодняшняя дата
    payment_date: Optional[date] = Field(
        default=None,
        description="Дата платежа (ДДС). По умолчанию — сегодня.",
    )

    # Для P&L: дата начисления может отличаться от даты платежа
    accrual_date: Optional[date] = Field(
        default=None,
        description="Дата начисления (P&L). Если не указана — равна payment_date.",
    )


# ---------------------------------------------------------------------------
# Схема ответа фронтенду
# ---------------------------------------------------------------------------


class AIParseResponseSchema(BaseModel):
    """
    Ответ эндпоинта parse-raw после успешного создания транзакции.
    Содержит как AI-классификацию, так и ID созданной записи.
    """

    # ID созданной транзакции в БД
    transaction_id: UUID

    # Финансовые данные — Decimal → str в JSON
    amount: DecimalStr
    currency: str = Field(default="RUB")

    # Классификация
    transaction_type: Literal["income", "expense", "transfer"]
    counterparty: Optional[str] = None
    description: str
    suggested_category_slug: str

    # Метрика качества
    confidence_score: float = Field(ge=0.0, le=1.0)

    # True — если Gemini был недоступен и использовался regex-парсер
    is_fallback: bool = False

    # Период
    payment_date: date
    accrual_date: Optional[date] = None
