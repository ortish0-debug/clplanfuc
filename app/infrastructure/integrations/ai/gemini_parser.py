"""
Реализация BaseAIParser на базе Google Gemini 2.5 Flash.

Поддерживаемые env-переменные:
  GEMINI_API_KEY         — обязательный API-ключ Google AI Studio / Vertex AI
  GEMINI_PROXY           — HTTP(S)-прокси для обхода гео-блокировок, например:
                           http://user:pass@proxy.example.com:8080
  GEMINI_API_ENDPOINT    — кастомный base URL (замена generativelanguage.googleapis.com)
                           для корпоративных прокси или зеркал

Стратегия отказоустойчивости:
  1. До 3 попыток с экспоненциальным бэкоффом (1s → 2s → 4s) при сетевых ошибках.
  2. Если все попытки исчерпаны — вызывается _fallback_parse() на регулярных выражениях
     с установкой флага is_fallback=True в результате.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from google import genai
from google.genai import types

from app.domain.schemas.ai_parser import GeminiResponseStructure
from app.infrastructure.integrations.ai.base import (
    BaseAIParser,
    BatchParseRequest,
    ParsedTransaction,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфигурация повторных попыток
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_DELAY_SEC = 1.0

# Подстроки в тексте исключения, при которых имеет смысл повторить запрос
_RETRYABLE_MARKERS = frozenset({
    "503", "502", "500", "429",
    "timeout", "timed out",
    "connection", "connect",
    "unavailable", "reset",
    "overloaded", "resource exhausted",
})

# ---------------------------------------------------------------------------
# Системный промпт — задаёт роль и формат ответа
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """Ты — высокоточный финансовый AI-ассистент для российского бизнеса.
Твоя единственная задача — извлечь и классифицировать финансовую операцию из произвольного текста.

ПРАВИЛА:
1. Поле "amount": сумма цифрами с точкой. Пробелы и пробельные разделители убрать.
   Примеры: "15000.00", "1250.50", "0.00" (если сумма не определена).
2. Поле "transaction_type":
   - "income"   — деньги ПОСТУПАЮТ на счёт (оплата от клиента, возврат, дивиденды).
   - "expense"  — деньги УХОДЯТ со счёта (оплата поставщику, аренда, зарплата, налоги).
   - "transfer" — перемещение между собственными счетами компании.
3. Поле "counterparty": название контрагента (ООО, ИП, ПАО, физлицо). null если неизвестно.
4. Поле "description": сжатое описание, максимум 100 символов, без суммы.
5. Поле "suggested_category_slug": ТОЛЬКО из предоставленного списка категорий.
   Если ни одна не подходит — "other".
6. Поле "confidence_score": 0.95+ если всё однозначно, 0.5–0.94 если есть неопределённость,
   ниже 0.5 если данных мало.

Отвечай ТОЛЬКО JSON в указанной структуре. Никаких пояснений вне JSON.
"""


# ---------------------------------------------------------------------------
# Класс парсера
# ---------------------------------------------------------------------------


class GeminiParser(BaseAIParser):
    """
    Реализация BaseAIParser на Gemini 2.5 Flash.

    Является stateless-синглтоном на уровне приложения:
    клиент инициализируется один раз при старте и переиспользуется.
    """

    model_id = "gemini-2.5-flash"

    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY не задан. "
                "Установите переменную окружения перед запуском приложения."
            )

        proxy_url = os.environ.get("GEMINI_PROXY", "").strip() or None
        api_endpoint = os.environ.get("GEMINI_API_ENDPOINT", "").strip() or None

        # ---------------------------------------------------------------
        # Прокси: устанавливаем через стандартные env-переменные httpx.
        # Если GEMINI_PROXY не задан явно — сбрасываем системные прокси,
        # чтобы httpx не подхватывал SOCKS из настроек Windows.
        # ---------------------------------------------------------------
        if proxy_url:
            os.environ["HTTPS_PROXY"] = proxy_url
            os.environ["HTTP_PROXY"]  = proxy_url
            os.environ["ALL_PROXY"]   = proxy_url
            logger.info("GeminiParser: прокси активен → %s", proxy_url)
        else:
            # Явно отключаем системный прокси для запросов к Google API.
            # httpx на Windows подхватывает SOCKS из реестра — это нежелательно.
            for _var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                         "https_proxy", "http_proxy", "all_proxy"):
                os.environ.pop(_var, None)
            os.environ["NO_PROXY"] = "googleapis.com,generativelanguage.googleapis.com"

        # ---------------------------------------------------------------
        # Кастомный эндпоинт: позволяет направить трафик через
        # корпоративный API-шлюз или зеркало вместо googleapis.com
        # ---------------------------------------------------------------
        http_options: Optional[types.HttpOptions] = None
        if api_endpoint:
            http_options = types.HttpOptions(base_url=api_endpoint)
            logger.info("GeminiParser: кастомный эндпоинт → %s", api_endpoint)

        self._client = genai.Client(
            api_key=api_key,
            http_options=http_options,
        )
        logger.info("GeminiParser: клиент инициализирован (model=%s)", self.model_id)

    # -----------------------------------------------------------------------
    # Построение промпта
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_user_prompt(text: str, categories: list[dict]) -> str:
        if categories:
            category_lines = "\n".join(
                f"  - {cat.get('slug', cat.get('icon', 'other'))}: "
                f"{cat.get('name', '')} "
                f"({cat.get('category_type', '')})"
                for cat in categories
            )
            categories_block = f"ДОСТУПНЫЕ КАТЕГОРИИ:\n{category_lines}"
        else:
            categories_block = "ДОСТУПНЫЕ КАТЕГОРИИ: только 'other'"

        return (
            f"ТЕКСТ ОПЕРАЦИИ ДЛЯ АНАЛИЗА:\n{text}\n\n"
            f"{categories_block}"
        )

    # -----------------------------------------------------------------------
    # Вызов Gemini API
    # -----------------------------------------------------------------------

    async def _call_gemini(
        self,
        user_prompt: str,
    ) -> GeminiResponseStructure:
        """Выполняет один вызов Gemini API и возвращает валидированный ответ."""
        response = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=GeminiResponseStructure,
                temperature=0.05,     # почти детерминированный режим
                max_output_tokens=512,
                # top_p и top_k не нужны при низком temperature
            ),
        )

        raw_text: str = response.text or ""
        if not raw_text.strip():
            raise ValueError("Gemini вернул пустой ответ.")

        return GeminiResponseStructure.model_validate_json(raw_text)

    # -----------------------------------------------------------------------
    # Определение, стоит ли повторять запрос
    # -----------------------------------------------------------------------

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """True — если ошибка носит временный сетевой/инфраструктурный характер."""
        msg = str(exc).lower()
        return any(marker in msg for marker in _RETRYABLE_MARKERS)

    # -----------------------------------------------------------------------
    # Fallback: regex-парсер
    # -----------------------------------------------------------------------

    @staticmethod
    async def _fallback_parse(text: str) -> GeminiResponseStructure:
        """
        Резервный парсер на регулярных выражениях.
        Вызывается только если все попытки Gemini исчерпаны.
        Возвращает результат с низкой confidence_score.
        """
        # --- Сумма ---
        # Поддерживает форматы: 1 500.00, 1500,00, 15 000 руб., 99₽
        amount_str = "0.00"
        amount_match = re.search(
            r"(\d[\d\s ]*(?:[.,]\d{1,2})?)\s*(?:руб(?:лей|ля)?|₽|rub\.?)?",
            text,
            re.IGNORECASE | re.UNICODE,
        )
        if amount_match:
            raw = (
                amount_match.group(1)
                .replace(" ", "")  # неразрывный пробел
                .replace(" ", "")
                .replace(",", ".")
            )
            try:
                amount_str = str(Decimal(raw).quantize(Decimal("0.01")))
            except InvalidOperation:
                amount_str = "0.00"

        # --- Тип операции ---
        text_lower = text.lower()

        _INCOME_KW = frozenset({
            "получ", "поступ", "приход", "зачислен", "выручк",
            "оплата от", "аванс от", "предоплат", "возврат от",
            "дивиденд", "доход",
        })
        _EXPENSE_KW = frozenset({
            "оплат", "списан", "расход", "перечислен", "платёж",
            "платеж", "покупк", "закупк", "аренд", "зарплат",
            "налог", "штраф", "комиссия", "абонент",
        })
        _TRANSFER_KW = frozenset({
            "перевод между", "перемещени", "переброс",
            "со счёта на счёт", "с р/с", "с расчётного",
        })

        txn_type: Literal["income", "expense", "transfer"]
        if any(kw in text_lower for kw in _TRANSFER_KW):
            txn_type = "transfer"
        elif any(kw in text_lower for kw in _INCOME_KW):
            txn_type = "income"
        elif any(kw in text_lower for kw in _EXPENSE_KW):
            txn_type = "expense"
        else:
            # По умолчанию расход, как наиболее частый тип
            txn_type = "expense"

        # --- Контрагент ---
        counterparty: Optional[str] = None
        # ООО «Ромашка», ИП Иванов, АО «Рога и Копыта», ПАО Сбербанк
        counterparty_match = re.search(
            r"(?:ООО|ОАО|ЗАО|АО|ПАО|НКО|ИП|ФИО?)"
            r"[\s«\"']*[\w\s\-«»\"'\.]{2,60}",
            text,
            re.IGNORECASE | re.UNICODE,
        )
        if counterparty_match:
            counterparty = re.sub(r"\s+", " ", counterparty_match.group(0)).strip()

        # --- Описание (первые 100 символов без лишних пробелов) ---
        description = re.sub(r"\s+", " ", text).strip()[:100]

        return GeminiResponseStructure(
            amount=amount_str,
            transaction_type=txn_type,
            counterparty=counterparty,
            description=description,
            suggested_category_slug="other",
            confidence_score=0.25,  # низкая уверенность — только regex
        )

    # -----------------------------------------------------------------------
    # Публичный метод с backoff + fallback
    # -----------------------------------------------------------------------

    async def classify_raw_text(
        self,
        text: str,
        available_categories: list[dict],
    ) -> tuple[GeminiResponseStructure, bool]:
        """
        Классифицирует произвольный текст операции.

        Возвращает:
          (GeminiResponseStructure, is_fallback: bool)

        Алгоритм:
          1. Попытка 1: немедленно
          2. Попытка 2: через 2 секунды (бэкофф 2^1)
          3. Попытка 3: через 4 секунды (бэкофф 2^2)
          4. Если все неудачны → _fallback_parse(), is_fallback=True
        """
        user_prompt = self._build_user_prompt(text, available_categories)
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            try:
                result = await self._call_gemini(user_prompt)
                if attempt > 0:
                    logger.info(
                        "GeminiParser: успех на попытке %d/%d",
                        attempt + 1, _MAX_RETRIES,
                    )
                return result, False

            except Exception as exc:
                last_exc = exc

                if not self._is_retryable(exc):
                    # Нефатальная, но не временная ошибка — нет смысла повторять
                    logger.warning(
                        "GeminiParser: нефатальная ошибка (attempt=%d): %s",
                        attempt + 1, exc,
                    )
                    break

                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY_SEC * (2 ** attempt)  # 1s, 2s
                    logger.warning(
                        "GeminiParser: попытка %d/%d завершилась ошибкой (%s). "
                        "Повтор через %.0f сек.",
                        attempt + 1, _MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "GeminiParser: попытка %d/%d завершилась ошибкой (%s). "
                        "Все попытки исчерпаны.",
                        attempt + 1, _MAX_RETRIES, exc,
                    )

        logger.error(
            "GeminiParser: переключение на regex-fallback. Причина: %s", last_exc
        )
        fallback_result = await self._fallback_parse(text)
        return fallback_result, True

    # -----------------------------------------------------------------------
    # Реализация интерфейса BaseAIParser
    # -----------------------------------------------------------------------

    async def classify_transaction(
        self,
        request: BatchParseRequest,
        available_categories: list[dict],
    ) -> ParsedTransaction:
        gemini_result, is_fallback = await self.classify_raw_text(
            request.description, available_categories
        )

        try:
            amount = Decimal(gemini_result.amount)
        except InvalidOperation:
            amount = Decimal("0.00")

        # Находим ID категории по slug (slug хранится в поле icon)
        category_id: Optional[UUID] = None
        category_name: Optional[str] = gemini_result.suggested_category_slug
        for cat in available_categories:
            if cat.get("slug") == gemini_result.suggested_category_slug or \
               cat.get("icon") == gemini_result.suggested_category_slug:
                category_id = cat.get("id")
                category_name = cat.get("name", category_name)
                break

        confidence = Decimal(
            str(gemini_result.confidence_score)
        ).quantize(Decimal("0.0001"))

        tags: list[str] = ["ai_classified"]
        if is_fallback:
            tags.append("fallback")

        return ParsedTransaction(
            suggested_category_id=category_id,
            suggested_category_name=category_name,
            confidence=confidence,
            tags=tags,
            reasoning=(
                "Regex fallback: Gemini API недоступен" if is_fallback else None
            ),
        )

    async def classify_batch(
        self,
        requests: list[BatchParseRequest],
        available_categories: list[dict],
    ) -> list[ParsedTransaction]:
        """
        Параллельная классификация пакета транзакций.
        Семафор ограничивает одновременные запросы к Gemini (защита от 429).
        """
        semaphore = asyncio.Semaphore(5)

        async def _classify_one(req: BatchParseRequest) -> ParsedTransaction:
            async with semaphore:
                return await self.classify_transaction(req, available_categories)

        return list(
            await asyncio.gather(*[_classify_one(r) for r in requests])
        )

    async def extract_from_document(
        self,
        document_content: bytes,
        mime_type: str,
    ) -> list[BatchParseRequest]:
        """
        Извлекает транзакции из документа (PDF-выписка, изображение чека).
        Использует мультимодальный режим Gemini.
        """
        if not document_content:
            return []

        supported_mime_types = {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "text/csv",
            "text/plain",
        }
        if mime_type not in supported_mime_types:
            logger.warning(
                "extract_from_document: неподдерживаемый MIME-тип %s", mime_type
            )
            return []

        extract_prompt = (
            "Извлеки все финансовые операции из документа. "
            "Для каждой операции верни: description (текст операции), "
            "counterparty (контрагент или null), amount (сумма строкой). "
            "Верни JSON-массив объектов."
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_id,
                contents=[
                    types.Part.from_bytes(data=document_content, mime_type=mime_type),
                    extract_prompt,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.05,
                    max_output_tokens=4096,
                ),
            )

            import json
            raw = response.text or "[]"
            items: list[dict] = json.loads(raw)

            result: list[BatchParseRequest] = []
            for item in items:
                try:
                    amount_raw = item.get("amount", "0")
                    amount = Decimal(str(amount_raw).replace(",", "."))
                except InvalidOperation:
                    amount = Decimal("0.00")

                result.append(
                    BatchParseRequest(
                        transaction_id=__import__("uuid").uuid4(),
                        description=str(item.get("description", ""))[:500],
                        counterparty=item.get("counterparty"),
                        amount=amount,
                        currency="RUB",
                    )
                )
            return result

        except Exception as exc:
            logger.error("extract_from_document: ошибка при обработке документа: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Ленивый синглтон — создаётся при первом обращении
# ---------------------------------------------------------------------------

_parser_instance: Optional[GeminiParser] = None


def get_gemini_parser() -> GeminiParser:
    """
    Возвращает переиспользуемый экземпляр GeminiParser.
    Используется как FastAPI-зависимость через Depends(get_gemini_parser).
    """
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = GeminiParser()
    return _parser_instance
