"""
Auto-categorization rules router.
CRUD для правил + endpoint «Применить ко всем транзакциям».
"""
from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Category, Transaction
from app.domain.models.rules import AutoRule, MatchField, MatchType
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/companies/{company_id}/auto-rules",
    tags=["Правила авто-категоризации"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    name: str
    field_to_match: str          # description | counterparty_name | counterparty_inn
    match_type: str              # contains | exact | regex
    pattern: str
    suggested_category_id: Optional[UUID] = None
    priority: int = 0
    is_active: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    field_to_match: Optional[str] = None
    match_type: Optional[str] = None
    pattern: Optional[str] = None
    suggested_category_id: Optional[UUID] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rule_to_dict(r: AutoRule, category_name: str = None) -> dict:
    return {
        "id":                    str(r.id),
        "name":                  r.name,
        "field_to_match":        r.field_to_match.value if hasattr(r.field_to_match, "value") else r.field_to_match,
        "match_type":            r.match_type.value if hasattr(r.match_type, "value") else r.match_type,
        "pattern":               r.pattern,
        "suggested_category_id": str(r.suggested_category_id) if r.suggested_category_id else None,
        "category_name":         category_name,
        "priority":              r.priority,
        "is_active":             r.is_active,
        "created_at":            r.created_at.isoformat(),
    }


def _matches(rule: AutoRule, tx: Transaction) -> bool:
    """Проверяет, подходит ли транзакция под правило."""
    field = rule.field_to_match.value if hasattr(rule.field_to_match, "value") else rule.field_to_match
    mtype = rule.match_type.value if hasattr(rule.match_type, "value") else rule.match_type

    if field == "description":
        value = tx.description or ""
    elif field == "counterparty_name":
        value = tx.counterparty or ""
    elif field == "counterparty_inn":
        value = getattr(tx, "counterparty_inn", "") or ""
    else:
        return False

    pattern = rule.pattern
    if mtype == "contains":
        return pattern.lower() in value.lower()
    elif mtype == "exact":
        return pattern.lower() == value.lower()
    elif mtype == "regex":
        try:
            return bool(re.search(pattern, value, re.IGNORECASE))
        except re.error:
            return False
    return False


async def _load_rules(db: AsyncSession, company_id: UUID) -> list[AutoRule]:
    res = await db.execute(
        select(AutoRule).where(
            and_(AutoRule.company_id == company_id, AutoRule.is_active.is_(True))
        ).order_by(AutoRule.priority.desc(), AutoRule.created_at)
    )
    return list(res.scalars().all())


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_rules(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список всех правил компании."""
    res = await db.execute(
        select(AutoRule, Category.name.label("cat_name"))
        .outerjoin(Category, AutoRule.suggested_category_id == Category.id)
        .where(AutoRule.company_id == company_id)
        .order_by(AutoRule.priority.desc(), AutoRule.created_at)
    )
    rows = res.all()
    return {"rules": [_rule_to_dict(r.AutoRule, r.cat_name) for r in rows]}


@router.post("/", status_code=201)
async def create_rule(
    company_id: UUID,
    payload: RuleCreate,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Создать новое правило."""
    rule = AutoRule(
        company_id=company_id,
        name=payload.name,
        field_to_match=MatchField(payload.field_to_match),
        match_type=MatchType(payload.match_type),
        pattern=payload.pattern,
        suggested_category_id=payload.suggested_category_id,
        priority=payload.priority,
        is_active=payload.is_active,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    # Получаем имя категории
    cat_name = None
    if rule.suggested_category_id:
        cr = await db.execute(select(Category.name).where(Category.id == rule.suggested_category_id))
        cat_name = cr.scalar_one_or_none()

    return _rule_to_dict(rule, cat_name)


@router.patch("/{rule_id}")
async def update_rule(
    company_id: UUID,
    rule_id: UUID,
    payload: RuleUpdate,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить правило (частично)."""
    res = await db.execute(
        select(AutoRule).where(and_(AutoRule.id == rule_id, AutoRule.company_id == company_id))
    )
    rule = res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")

    if payload.name is not None:
        rule.name = payload.name
    if payload.field_to_match is not None:
        rule.field_to_match = MatchField(payload.field_to_match)
    if payload.match_type is not None:
        rule.match_type = MatchType(payload.match_type)
    if payload.pattern is not None:
        rule.pattern = payload.pattern
    if payload.suggested_category_id is not None:
        rule.suggested_category_id = payload.suggested_category_id
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.is_active is not None:
        rule.is_active = payload.is_active

    await db.commit()
    await db.refresh(rule)

    cat_name = None
    if rule.suggested_category_id:
        cr = await db.execute(select(Category.name).where(Category.id == rule.suggested_category_id))
        cat_name = cr.scalar_one_or_none()

    return _rule_to_dict(rule, cat_name)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    company_id: UUID,
    rule_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить правило."""
    res = await db.execute(
        select(AutoRule).where(and_(AutoRule.id == rule_id, AutoRule.company_id == company_id))
    )
    rule = res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(rule)
    await db.commit()


# ── Apply to transactions ─────────────────────────────────────────────────────

@router.post("/apply-all")
async def apply_rules_to_all(
    company_id: UUID,
    only_uncategorized: bool = True,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Прогоняет все активные правила через транзакции компании.
    По умолчанию обрабатывает только транзакции без категории.
    Возвращает количество обновлённых транзакций.
    """
    rules = await _load_rules(db, company_id)
    if not rules:
        return {"updated": 0, "message": "Нет активных правил"}

    # Загружаем транзакции
    filters = [
        Transaction.company_id == company_id,
        Transaction.is_deleted.is_(False),
    ]
    if only_uncategorized:
        filters.append(Transaction.category_id.is_(None))

    txn_res = await db.execute(select(Transaction).where(and_(*filters)))
    transactions = list(txn_res.scalars().all())

    updated = 0
    details = []
    for tx in transactions:
        for rule in rules:
            if _matches(rule, tx):
                tx.category_id = rule.suggested_category_id
                updated += 1
                details.append({
                    "transaction_id": str(tx.id),
                    "description":    tx.description,
                    "rule_name":      rule.name,
                    "category_id":    str(rule.suggested_category_id) if rule.suggested_category_id else None,
                })
                break  # первое совпавшее правило

    if updated:
        await db.commit()

    return {
        "updated": updated,
        "total_checked": len(transactions),
        "details": details[:50],  # max 50 в ответе
    }


@router.post("/preview")
async def preview_rule(
    company_id: UUID,
    payload: RuleCreate,
    limit: int = 20,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Предпросмотр: показывает какие транзакции совпадут с правилом
    без фактического обновления.
    """
    # Создаём временный объект (не сохраняем в БД)
    rule = AutoRule(
        company_id=company_id,
        name=payload.name,
        field_to_match=MatchField(payload.field_to_match),
        match_type=MatchType(payload.match_type),
        pattern=payload.pattern,
        suggested_category_id=payload.suggested_category_id,
        priority=payload.priority,
        is_active=True,
    )

    txn_res = await db.execute(
        select(Transaction).where(
            and_(Transaction.company_id == company_id, Transaction.is_deleted.is_(False))
        ).limit(500)
    )
    transactions = list(txn_res.scalars().all())

    matched = [
        {"id": str(tx.id), "description": tx.description, "amount": float(tx.amount), "payment_date": str(tx.payment_date)}
        for tx in transactions if _matches(rule, tx)
    ][:limit]

    return {"matched_count": len(matched), "matches": matched}
