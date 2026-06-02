"""
Сервис: Расчёт НДС (Спринт 10).

Реализует две базовые формулы российского НДС:
  - Экстракция «изнутри»: НДС уже включён в сумму (типичный сценарий для УПД)
  - Начисление «сверху»:  НДС добавляется к сумме без налога (для спецификаций)

Допустимые ставки НДС по НК РФ (2024):
  0.00  — экспорт, международные перевозки, ряд льготных операций
  10.00 — продовольствие, товары для детей, медицинские изделия, книги
  20.00 — общая ставка (всё остальное)
  NULL  — операции без НДС (УСН, ОСНО с освобождением по ст. 149 НК РФ)

Все расчёты ведутся в Decimal с округлением HALF_UP до копеек.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# Допустимые ставки по НК РФ
_VALID_VAT_RATES = frozenset({Decimal("0.00"), Decimal("10.00"), Decimal("20.00")})


def _q(v: Decimal) -> Decimal:
    """Округляет до копейки по правилу HALF_UP."""
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# КЛЮЧЕВЫЕ РАСЧЁТНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


def calculate_vat_extraction(
    amount:   Decimal,
    vat_rate: Optional[Decimal],
) -> dict:
    """
    НДС «изнутри» — ставка включена в переданную сумму (включая НДС).

    Формула:
        vat_amount = amount × rate / (100 + rate)
        amount_without_vat = amount − vat_amount

    Примеры:
        120 000 ₽ при ставке 20% → НДС = 20 000 ₽, без НДС = 100 000 ₽
        110 000 ₽ при ставке 10% → НДС = 10 000 ₽, без НДС = 100 000 ₽
        100 000 ₽ при ставке  0% → НДС =      0 ₽, без НДС = 100 000 ₽
        100 000 ₽ при ставке NULL → НДС не применяется

    Аргументы:
        amount   — сумма С учётом НДС (сумма в документе или транзакции)
        vat_rate — ставка НДС в % (20.00, 10.00, 0.00) или None

    Возвращает dict:
        amount_with_vat:    исходная сумма (с НДС)
        amount_without_vat: сумма без НДС (налоговая база)
        vat_amount:         выделенная сумма налога
        vat_rate:           применённая ставка (None если без НДС)
        has_vat:            True если ставка задана и > 0
    """
    amount = _q(amount)

    if vat_rate is None:
        return {
            "amount_with_vat":    amount,
            "amount_without_vat": amount,
            "vat_amount":         ZERO,
            "vat_rate":           None,
            "has_vat":            False,
        }

    rate = _q(vat_rate)
    if rate == ZERO:
        # Ставка 0% — НДС ноль, но признак НДС присутствует (для книги продаж)
        return {
            "amount_with_vat":    amount,
            "amount_without_vat": amount,
            "vat_amount":         ZERO,
            "vat_rate":           rate,
            "has_vat":            False,
        }

    # vat = amount × rate / (100 + rate)
    vat_amount         = _q(amount * rate / (Decimal("100") + rate))
    amount_without_vat = _q(amount - vat_amount)

    return {
        "amount_with_vat":    amount,
        "amount_without_vat": amount_without_vat,
        "vat_amount":         vat_amount,
        "vat_rate":           rate,
        "has_vat":            True,
    }


def calculate_vat_addition(
    amount_without_vat: Decimal,
    vat_rate:           Optional[Decimal],
) -> dict:
    """
    НДС «сверху» — ставка добавляется к сумме без налога.

    Формула:
        vat_amount = amount_without_vat × rate / 100
        amount_with_vat = amount_without_vat + vat_amount

    Аргументы:
        amount_without_vat — налоговая база (сумма без НДС)
        vat_rate           — ставка НДС в %

    Возвращает dict с теми же ключами, что и calculate_vat_extraction.
    """
    base = _q(amount_without_vat)

    if vat_rate is None:
        return {
            "amount_with_vat":    base,
            "amount_without_vat": base,
            "vat_amount":         ZERO,
            "vat_rate":           None,
            "has_vat":            False,
        }

    rate       = _q(vat_rate)
    vat_amount = _q(base * rate / Decimal("100"))
    total      = _q(base + vat_amount)

    return {
        "amount_with_vat":    total,
        "amount_without_vat": base,
        "vat_amount":         vat_amount,
        "vat_rate":           rate,
        "has_vat":            rate > ZERO,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────


def validate_vat_rate(vat_rate: Optional[Decimal]) -> Optional[Decimal]:
    """
    Проверяет допустимость ставки НДС по НК РФ.

    Допустимо: None, 0.00, 10.00, 20.00.

    Raises:
        ValueError: если ставка задана, но не соответствует НК РФ.
    """
    if vat_rate is None:
        return None
    rate = _q(vat_rate)
    if rate not in _VALID_VAT_RATES:
        raise ValueError(
            f"Недопустимая ставка НДС {rate}%. "
            "По НК РФ допустимы: 0%, 10%, 20% или отсутствие НДС (None)."
        )
    return rate


def batch_calculate_vat(
    amounts:  list[Decimal],
    vat_rate: Optional[Decimal],
) -> dict:
    """
    Агрегирует расчёт НДС по нескольким суммам (например, для отчёта).

    Возвращает:
        total_with_vat, total_without_vat, total_vat
    """
    total_with    = ZERO
    total_without = ZERO
    total_vat     = ZERO

    for amount in amounts:
        r = calculate_vat_extraction(amount, vat_rate)
        total_with    += r["amount_with_vat"]
        total_without += r["amount_without_vat"]
        total_vat     += r["vat_amount"]

    return {
        "total_with_vat":    _q(total_with),
        "total_without_vat": _q(total_without),
        "total_vat_amount":  _q(total_vat),
        "vat_rate":          vat_rate,
        "count":             len(amounts),
    }
