"""Russian tax forms and declarations service."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company, Transaction, TaxRegime
from app.domain.models.documents import Document


async def _get_company_regime(db: AsyncSession, company_id: UUID) -> str:
    """Вернуть код налогового режима компании из БД."""
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if company and company.tax_regime:
        return company.tax_regime.value
    return TaxRegime.USN_INCOME.value


async def calculate_tax_declaration(db: AsyncSession, company_id: UUID, tax_type: str) -> dict:
    """
    Рассчитать налоговую декларацию с учётом режима компании.

    Args:
        tax_type: 'usn' (УСН), 'vat' (НДС 20%), '3ndfl' (3-НДФЛ)
    """
    current_year = datetime.now().year
    regime = await _get_company_regime(db, company_id)

    # Общие доходы и расходы за год
    income_result = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.company_id == company_id,
            Transaction.transaction_type == 'income',
            func.extract('year', Transaction.created_at) == current_year,
        )
    )
    expense_result = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.company_id == company_id,
            Transaction.transaction_type == 'expense',
            func.extract('year', Transaction.created_at) == current_year,
        )
    )
    total_income = Decimal(str(income_result.scalar() or 0))
    total_expenses = Decimal(str(expense_result.scalar() or 0))
    profit = max(Decimal("0"), total_income - total_expenses)

    if tax_type == 'usn':
        # Расчёт в зависимости от реального режима компании
        if regime in ('usn_income',):
            # УСН «Доходы» 6%
            tax_before = total_income * Decimal("0.06")
            # Страховые взносы снижают налог до 50%
            insurance = total_income * Decimal("0.30")
            max_deduction = tax_before * Decimal("0.50")
            actual_deduction = min(insurance, max_deduction)
            calculated_tax = max(Decimal("0"), tax_before - actual_deduction)
            return {
                "tax_type": "usn",
                "regime": "УСН «Доходы» 6%",
                "tax_base": float(total_income),
                "tax_rate": 0.06,
                "tax_before_deduction": round(float(tax_before), 2),
                "insurance_deduction": round(float(actual_deduction), 2),
                "calculated_tax": round(float(calculated_tax), 2),
                "status": "generated",
            }

        elif regime in ('usn_profit', 'usn_income_minus_expenses'):
            # УСН «Доходы − Расходы» 15%
            standard_tax = profit * Decimal("0.15")
            minimum_tax = total_income * Decimal("0.01")
            calculated_tax = max(standard_tax, minimum_tax)
            return {
                "tax_type": "usn",
                "regime": "УСН «Доходы − Расходы» 15%",
                "tax_base": float(profit),
                "income": float(total_income),
                "expenses": float(total_expenses),
                "tax_rate": 0.15,
                "minimum_tax_1pct": round(float(minimum_tax), 2),
                "calculated_tax": round(float(calculated_tax), 2),
                "status": "generated",
            }

        elif regime in ('osn', 'osno'):
            # ОСНО — налог на прибыль 20%
            calculated_tax = profit * Decimal("0.20")
            return {
                "tax_type": "usn",
                "regime": "ОСНО — налог на прибыль 20%",
                "tax_base": float(profit),
                "income": float(total_income),
                "expenses": float(total_expenses),
                "tax_rate": 0.20,
                "calculated_tax": round(float(calculated_tax), 2),
                "status": "generated",
            }

        elif regime == 'eshn':
            # ЕСХН 6% от прибыли
            calculated_tax = profit * Decimal("0.06")
            return {
                "tax_type": "usn",
                "regime": "ЕСХН 6%",
                "tax_base": float(profit),
                "income": float(total_income),
                "expenses": float(total_expenses),
                "tax_rate": 0.06,
                "calculated_tax": round(float(calculated_tax), 2),
                "status": "generated",
            }

        elif regime == 'npd':
            # НПД 6% от дохода (юрлица)
            calculated_tax = total_income * Decimal("0.06")
            return {
                "tax_type": "usn",
                "regime": "НПД (самозанятый) 6%",
                "tax_base": float(total_income),
                "tax_rate": 0.06,
                "calculated_tax": round(float(calculated_tax), 2),
                "note": "Ставка 4% если все клиенты — физлица",
                "status": "generated",
            }

        elif regime == 'patent':
            return {
                "tax_type": "usn",
                "regime": "ПСН (Патент)",
                "tax_base": 0,
                "calculated_tax": 0,
                "note": "Сумма патента устанавливается региональным законодательством. Рассчитайте на сайте ФНС: patent.nalog.ru",
                "status": "generated",
            }

        # Дефолт — УСН 6%
        calculated_tax = total_income * Decimal("0.06")
        return {
            "tax_type": "usn",
            "regime": regime,
            "tax_base": float(total_income),
            "calculated_tax": round(float(calculated_tax), 2),
            "status": "generated",
        }

    elif tax_type == 'vat':
        # НДС только для ОСНО; для остальных — не применяется
        if regime not in ('osn', 'osno'):
            return {
                "tax_type": "vat",
                "regime": regime,
                "calculated_tax": 0,
                "note": f"НДС не применяется для режима «{regime}». НДС платят только компании на ОСНО.",
                "status": "not_applicable",
            }

        outbound_result = await db.execute(
            select(func.sum(Document.total_amount)).where(
                Document.company_id == company_id,
                Document.status == 'paid',
                func.extract('year', Document.created_at) == current_year,
            )
        )
        outbound_amount = float(outbound_result.scalar() or 0)
        outbound_vat = outbound_amount * 0.20
        inbound_vat = outbound_vat * 0.50  # примерный входящий НДС
        vat_to_pay = max(0, outbound_vat - inbound_vat)

        return {
            "tax_type": "vat",
            "regime": "ОСНО — НДС 20%",
            "outbound_amount": outbound_amount,
            "outbound_vat": round(outbound_vat, 2),
            "inbound_vat": round(inbound_vat, 2),
            "calculated_tax": round(vat_to_pay, 2),
            "status": "generated",
        }

    elif tax_type == '3ndfl':
        # 3-НДФЛ: 13% — только для ИП на ОСНО / владельцев компаний
        ndfl_tax = float(total_income) * 0.13
        return {
            "tax_type": "3ndfl",
            "regime": regime,
            "entrepreneur_income": float(total_income),
            "ndfl_rate": 0.13,
            "calculated_tax": round(ndfl_tax, 2),
            "note": "3-НДФЛ подаётся ИП на ОСНО и физлицами при получении дохода от бизнеса",
            "status": "generated",
        }

    return {"status": "error", "message": f"Unknown tax type: {tax_type}"}
