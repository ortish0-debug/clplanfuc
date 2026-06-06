"""Global search endpoint."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.counterparties import Counterparty
from app.domain.models.crm_deals import Deal
from app.domain.models.finance import Transaction
from app.domain.models.projects import Project
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db

router = APIRouter(prefix="/companies/{company_id}/search", tags=["Поиск"])


@router.get("/")
async def global_search(
    company_id: UUID,
    q: str = Query(..., min_length=1, max_length=100),
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Поиск по транзакциям, контрагентам, сделкам и проектам."""
    # SQLite lower() не работает с кириллицей — ищем все варианты регистра
    q_clean = q.strip()
    patterns = list({
        f"%{q_clean}%",          # оригинал
        f"%{q_clean.lower()}%",  # строчные
        f"%{q_clean.upper()}%",  # ЗАГЛАВНЫЕ
        f"%{q_clean.title()}%",  # Title Case (каждое слово с заглавной)
        f"%{q_clean.capitalize()}%",  # Первое слово с заглавной
    })

    def cyrillic_like(*cols):
        """OR по всем вариантам регистра для каждой колонки."""
        conditions = []
        for col in cols:
            for pat in patterns:
                conditions.append(col.like(pat))
        return or_(*conditions)

    results = {"transactions": [], "counterparties": [], "deals": [], "projects": []}

    # ── Транзакции ──────────────────────────────────────────────────────────
    txn_res = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
                cyrillic_like(Transaction.description, Transaction.counterparty),
            )
        ).order_by(Transaction.payment_date.desc()).limit(5)
    )
    for t in txn_res.scalars().all():
        results["transactions"].append({
            "id":          str(t.id),
            "type":        "transaction",
            "title":       t.description or "Без описания",
            "subtitle":    t.payment_date.strftime("%d.%m.%Y"),
            "amount":      float(t.amount),
            "tx_type":     t.transaction_type.value,
            "hash":        "#transactions",
        })

    # ── Контрагенты ─────────────────────────────────────────────────────────
    cp_res = await db.execute(
        select(Counterparty).where(
            and_(
                Counterparty.company_id == company_id,
                Counterparty.is_deleted.is_(False),
                cyrillic_like(Counterparty.name, Counterparty.inn),
            )
        ).limit(5)
    )
    for c in cp_res.scalars().all():
        results["counterparties"].append({
            "id":       str(c.id),
            "type":     "counterparty",
            "title":    c.name,
            "subtitle": f"ИНН: {c.inn}" if c.inn else ("Клиент" if c.is_customer else "Поставщик"),
            "hash":     "#counterparties",
        })

    # ── CRM Сделки ──────────────────────────────────────────────────────────
    deal_res = await db.execute(
        select(Deal).where(
            and_(
                Deal.company_id == company_id,
                cyrillic_like(Deal.title, Deal.counterparty_name),
            )
        ).limit(5)
    )
    STATUS_RU = {"lead": "Лид", "contact": "Контакт", "proposal": "КП",
                 "negotiation": "Переговоры", "won": "Выиграно", "lost": "Проиграно"}
    for d in deal_res.scalars().all():
        results["deals"].append({
            "id":       str(d.id),
            "type":     "deal",
            "title":    d.title,
            "subtitle": STATUS_RU.get(d.status.value, d.status.value)
                        + (f" · {d.counterparty_name}" if d.counterparty_name else ""),
            "amount":   float(d.amount) if d.amount else None,
            "hash":     "#crm",
        })

    # ── Проекты ─────────────────────────────────────────────────────────────
    proj_res = await db.execute(
        select(Project).where(
            and_(
                Project.company_id == company_id,
                Project.is_deleted.is_(False),
                cyrillic_like(Project.name, Project.description),
            )
        ).limit(5)
    )
    for p in proj_res.scalars().all():
        results["projects"].append({
            "id":       str(p.id),
            "type":     "project",
            "title":    p.name,
            "subtitle": p.status or "",
            "hash":     "#projects",
        })

    total = sum(len(v) for v in results.values())
    return {"query": q, "total": total, "results": results}
