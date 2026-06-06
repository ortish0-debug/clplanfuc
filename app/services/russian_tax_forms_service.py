"""Russian tax forms and declarations service."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Transaction
from app.domain.models.documents import Document


async def calculate_tax_declaration(db: AsyncSession, company_id: UUID, tax_type: str) -> dict:
    """
    Calculate Russian tax declaration.

    Args:
        tax_type: 'usn' (УСН 6%), 'vat' (НДС 20%), '3ndfl' (3-НДФЛ)

    Returns:
        {tax_type, tax_base, calculated_tax, status}
    """
    current_year = datetime.now().year

    if tax_type == 'usn':
        # Simplified Tax System (УСН) - 6% on income
        result = await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.company_id == company_id,
                Transaction.transaction_type == 'income',
                func.extract('year', Transaction.created_at) == current_year,
            )
        )
        tax_base = float(result.scalar() or 0)

        # Reduce by insurance contributions (simulate: 30% for SMB)
        insurance_payments = tax_base * 0.30
        taxable_base = max(0, tax_base - insurance_payments)

        calculated_tax = taxable_base * 0.06

        return {
            "tax_type": "usn",
            "tax_base": tax_base,
            "insurance_contributions": insurance_payments,
            "calculated_tax": round(calculated_tax, 2),
            "status": "generated",
        }

    elif tax_type == 'vat':
        # Value Added Tax (НДС) 20%
        # Outbound VAT: 20% from paid documents
        outbound_result = await db.execute(
            select(func.sum(Document.total_amount)).where(
                Document.company_id == company_id,
                Document.status == 'paid',
                func.extract('year', Document.created_at) == current_year,
            )
        )
        outbound_amount = float(outbound_result.scalar() or 0)
        outbound_vat = outbound_amount * 0.20

        # Inbound VAT: simulate 50% of outbound (typical for service companies)
        inbound_vat = outbound_vat * 0.50
        vat_to_pay = outbound_vat - inbound_vat

        return {
            "tax_type": "vat",
            "outbound_amount": outbound_amount,
            "outbound_vat": round(outbound_vat, 2),
            "inbound_vat": round(inbound_vat, 2),
            "calculated_tax": round(vat_to_pay, 2),
            "status": "generated",
        }

    elif tax_type == '3ndfl':
        # Personal Income Tax (3-НДФЛ) for entrepreneurs
        # Aggregate income from payroll data (simulation)
        result = await db.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.company_id == company_id,
                Transaction.transaction_type == 'income',
                func.extract('year', Transaction.created_at) == current_year,
            )
        )
        entrepreneur_income = float(result.scalar() or 0)

        # 3-НДФЛ: 13% standard rate
        ndfl_tax = entrepreneur_income * 0.13

        return {
            "tax_type": "3ndfl",
            "entrepreneur_income": entrepreneur_income,
            "ndfl_rate": 0.13,
            "calculated_tax": round(ndfl_tax, 2),
            "status": "generated",
        }

    return {"status": "error", "message": f"Unknown tax type: {tax_type}"}
