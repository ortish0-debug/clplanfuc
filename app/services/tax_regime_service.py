"""
Сервис налоговых расчётов в зависимости от режима компании.
Поддерживает: ОСНО, УСН 6%, УСН 15%, ПСН, ЕСХН, НПД.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company, TaxRegime

# ---------------------------------------------------------------------------
# Справочник налоговых режимов
# ---------------------------------------------------------------------------

TAX_REGIME_INFO: dict[str, dict[str, str]] = {
    TaxRegime.OSN.value: {
        "code": "osn",
        "name": "ОСНО",
        "description": "Общая система налогообложения",
        "rate": "НДС 20% + налог на прибыль 20% (или НДФЛ 13%)",
        "income_tax_rate": "0.20",
        "vat_rate": "0.20",
    },
    TaxRegime.USN_INCOME.value: {
        "code": "usn_income",
        "name": "УСН «Доходы»",
        "description": "Упрощённая система — налог только с доходов",
        "rate": "6% от доходов",
        "income_tax_rate": "0.06",
        "vat_rate": "0.00",
    },
    TaxRegime.USN_PROFIT.value: {
        "code": "usn_profit",
        "name": "УСН «Доходы минус расходы»",
        "description": "Упрощённая система — налог с прибыли",
        "rate": "15% от (доходы − расходы), минимум 1% от доходов",
        "income_tax_rate": "0.15",
        "vat_rate": "0.00",
        "min_rate": "0.01",
    },
    TaxRegime.PATENT.value: {
        "code": "patent",
        "name": "ПСН (Патент)",
        "description": "Патентная система для ИП — фиксированный налог",
        "rate": "6% от потенциального дохода (устанавливается регионом)",
        "income_tax_rate": "0.06",
        "vat_rate": "0.00",
    },
    TaxRegime.ESHN.value: {
        "code": "eshn",
        "name": "ЕСХН",
        "description": "Единый сельскохозяйственный налог",
        "rate": "6% от (доходы − расходы)",
        "income_tax_rate": "0.06",
        "vat_rate": "0.00",
    },
    TaxRegime.NPD.value: {
        "code": "npd",
        "name": "НПД (Самозанятый)",
        "description": "Налог на профессиональный доход",
        "rate": "4% от физлиц, 6% от юрлиц",
        "income_tax_rate_individuals": "0.04",
        "income_tax_rate_legal": "0.06",
        "vat_rate": "0.00",
    },
}


def get_all_regimes() -> list[dict[str, str]]:
    """Вернуть список всех поддерживаемых налоговых режимов."""
    return list(TAX_REGIME_INFO.values())


def get_regime_info(regime: str) -> dict[str, str]:
    """Вернуть информацию о конкретном режиме."""
    return TAX_REGIME_INFO.get(regime, {})


# ---------------------------------------------------------------------------
# Расчёт налога
# ---------------------------------------------------------------------------

def calculate_tax(
    regime: str,
    income: Decimal,
    expenses: Decimal = Decimal("0"),
    from_individuals: bool = True,
) -> dict[str, Any]:
    """
    Рассчитать налог для компании в зависимости от режима.

    Args:
        regime: код налогового режима
        income: сумма доходов
        expenses: сумма расходов (для УСН 15%, ОСНО, ЕСХН)
        from_individuals: для НПД — True если доход от физлиц (4%), False от юрлиц (6%)

    Returns:
        dict с полями: tax_amount, tax_base, rate, details
    """
    income = Decimal(str(income))
    expenses = Decimal(str(expenses))
    profit = max(income - expenses, Decimal("0"))

    if regime == TaxRegime.USN_INCOME.value:
        rate = Decimal("0.06")
        tax = income * rate
        return {
            "regime": regime,
            "tax_base": income,
            "rate": "6%",
            "tax_amount": round(tax, 2),
            "details": "УСН Доходы: 6% × доходы",
        }

    elif regime == TaxRegime.USN_PROFIT.value:
        rate = Decimal("0.15")
        min_rate = Decimal("0.01")
        tax_standard = profit * rate
        tax_minimum = income * min_rate
        tax = max(tax_standard, tax_minimum)
        return {
            "regime": regime,
            "tax_base": profit,
            "rate": "15% (мин. 1%)",
            "tax_amount": round(tax, 2),
            "details": f"УСН Доходы−Расходы: max(15%×прибыль={round(tax_standard,2)}, 1%×доходы={round(tax_minimum,2)})",
        }

    elif regime == TaxRegime.OSN.value:
        profit_tax_rate = Decimal("0.20")
        vat_rate = Decimal("0.20")
        profit_tax = profit * profit_tax_rate
        vat = income * vat_rate
        return {
            "regime": regime,
            "tax_base": profit,
            "rate": "НДС 20% + налог на прибыль 20%",
            "tax_amount": round(profit_tax + vat, 2),
            "profit_tax": round(profit_tax, 2),
            "vat": round(vat, 2),
            "details": f"ОСНО: налог на прибыль={round(profit_tax,2)}, НДС={round(vat,2)}",
        }

    elif regime == TaxRegime.PATENT.value:
        return {
            "regime": regime,
            "tax_base": Decimal("0"),
            "rate": "Фиксированный (6% от потенциального дохода)",
            "tax_amount": Decimal("0"),
            "details": "ПСН: сумма патента устанавливается региональным законодательством",
        }

    elif regime == TaxRegime.ESHN.value:
        rate = Decimal("0.06")
        tax = profit * rate
        return {
            "regime": regime,
            "tax_base": profit,
            "rate": "6%",
            "tax_amount": round(tax, 2),
            "details": f"ЕСХН: 6% × (доходы−расходы)={round(profit,2)}",
        }

    elif regime == TaxRegime.NPD.value:
        rate = Decimal("0.04") if from_individuals else Decimal("0.06")
        tax = income * rate
        rate_label = "4%" if from_individuals else "6%"
        return {
            "regime": regime,
            "tax_base": income,
            "rate": rate_label,
            "tax_amount": round(tax, 2),
            "details": f"НПД: {rate_label} × доходы ({'физлица' if from_individuals else 'юрлица'})",
        }

    return {
        "regime": regime,
        "tax_base": Decimal("0"),
        "rate": "—",
        "tax_amount": Decimal("0"),
        "details": "Неизвестный налоговый режим",
    }


# ---------------------------------------------------------------------------
# DB-операции
# ---------------------------------------------------------------------------

async def get_company_tax_regime(db: AsyncSession, company_id: UUID) -> dict:
    """Вернуть текущий налоговый режим компании."""
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        return {}
    return {
        "company_id": str(company_id),
        "tax_regime": company.tax_regime.value,
        **get_regime_info(company.tax_regime.value),
    }


async def update_company_tax_regime(
    db: AsyncSession,
    company_id: UUID,
    new_regime: str,
) -> dict:
    """Сменить налоговый режим компании."""
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise ValueError(f"Компания {company_id} не найдена")

    company.tax_regime = TaxRegime(new_regime)
    await db.commit()
    await db.refresh(company)

    return {
        "company_id": str(company_id),
        "tax_regime": company.tax_regime.value,
        **get_regime_info(company.tax_regime.value),
        "message": f"Налоговый режим изменён на «{get_regime_info(new_regime).get('name', new_regime)}»",
    }
