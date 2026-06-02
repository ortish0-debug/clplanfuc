"""Transaction Templates router."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType
from app.domain.models.templates import TransactionTemplate
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db

router = APIRouter(prefix="/companies/{company_id}/templates", tags=["Шаблоны операций"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    amount: float
    transaction_type: str  # income | expense
    account_id: Optional[UUID] = None
    category_id: Optional[UUID] = None


class TemplateApply(BaseModel):
    payment_date: Optional[str] = None   # YYYY-MM-DD, default today
    amount: Optional[float] = None       # override amount if needed
    description: Optional[str] = None   # override description if needed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tpl_to_dict(t: TransactionTemplate, account_name: str = None) -> dict:
    return {
        "id":               str(t.id),
        "company_id":       str(t.company_id),
        "name":             t.name,
        "description":      t.description,
        "amount":           float(t.amount),
        "transaction_type": t.transaction_type,
        "account_id":       str(t.account_id) if t.account_id else None,
        "account_name":     account_name,
        "category_id":      str(t.category_id) if t.category_id else None,
        "created_at":       t.created_at.isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/")
async def list_templates(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all templates for company."""
    result = await db.execute(
        select(TransactionTemplate, Account.name.label("account_name"))
        .outerjoin(Account, TransactionTemplate.account_id == Account.id)
        .where(TransactionTemplate.company_id == company_id)
        .order_by(TransactionTemplate.created_at.desc())
    )
    rows = result.all()
    return {"templates": [_tpl_to_dict(r.TransactionTemplate, r.account_name) for r in rows]}


@router.post("/", status_code=201)
async def create_template(
    company_id: UUID,
    payload: TemplateCreate,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new template."""
    tpl = TransactionTemplate(
        company_id=company_id,
        name=payload.name,
        description=payload.description,
        amount=Decimal(str(payload.amount)),
        transaction_type=payload.transaction_type.lower(),
        account_id=payload.account_id,
        category_id=payload.category_id,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return _tpl_to_dict(tpl)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    company_id: UUID,
    template_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a template."""
    result = await db.execute(
        select(TransactionTemplate).where(
            and_(TransactionTemplate.id == template_id,
                 TransactionTemplate.company_id == company_id)
        )
    )
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    await db.delete(tpl)
    await db.commit()


@router.post("/{template_id}/apply", status_code=201)
async def apply_template(
    company_id: UUID,
    template_id: UUID,
    payload: TemplateApply,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a transaction from a template."""
    result = await db.execute(
        select(TransactionTemplate).where(
            and_(TransactionTemplate.id == template_id,
                 TransactionTemplate.company_id == company_id)
        )
    )
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    # Resolve account
    account_id = tpl.account_id
    if not account_id:
        # Use first account of company
        acc_res = await db.execute(
            select(Account).where(
                and_(Account.company_id == company_id, Account.is_deleted.is_(False))
            ).limit(1)
        )
        acc = acc_res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=400, detail="Нет доступных счетов")
        account_id = acc.id

    acc_res = await db.execute(select(Account).where(Account.id == account_id))
    account = acc_res.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=400, detail="Счёт шаблона не найден")

    amount = Decimal(str(payload.amount)) if payload.amount else tpl.amount
    description = payload.description or tpl.description or tpl.name
    pay_date = date.fromisoformat(payload.payment_date) if payload.payment_date else date.today()
    tx_type = TransactionType.INCOME if tpl.transaction_type == "income" else TransactionType.EXPENSE

    txn = Transaction(
        company_id=company_id,
        account_id=account_id,
        category_id=tpl.category_id,
        transaction_type=tx_type,
        amount=amount,
        amount_base_currency=amount,
        currency=account.currency,
        exchange_rate=Decimal("1.000000"),
        description=description,
        payment_date=pay_date,
        status=TransactionStatus.CONFIRMED,
        tags=["from_template"],
        meta={"template_id": str(template_id), "template_name": tpl.name},
        ai_classified=False,
        is_deleted=False,
        is_intra_group=False,
    )
    db.add(txn)

    # Update account balance
    if tx_type == TransactionType.INCOME:
        account.current_balance += amount
    else:
        account.current_balance -= amount

    await db.commit()
    return {
        "status": "created",
        "transaction_id": str(txn.id),
        "amount": float(amount),
        "description": description,
        "payment_date": pay_date.isoformat(),
        "transaction_type": tpl.transaction_type,
    }
