"""
Сервис: Бюджетирование — отчёт «План vs Факт».

Собирает дерево категорий компании, вытягивает план из таблицы `budgets`
и рассчитывает факт как сумму транзакций по дате начисления (accrual_date)
за указанный месяц. Поддерживает опциональную фильтрацию по project_id.
"""
from __future__ import annotations

import calendar as cal_module
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Category,
    CategoryType,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.domain.models.projects import Budget

ZERO = Decimal("0.00")


def _q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# СТРУКТУРЫ РЕЗУЛЬТАТА
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CategoryPlanFact:
    """Одна строка отчёта Плана-Факта для категории."""
    category_id:   UUID
    category_name: str
    category_type: str            # "income" | "expense"
    parent_id:     Optional[UUID]
    depth:         int             # 0 — корень, 1 — первый уровень вложенности

    plan_amount:   Decimal = ZERO
    fact_amount:   Decimal = ZERO

    @property
    def deviation(self) -> Decimal:
        """Абсолютное отклонение: факт − план (>0 — перерасход/недопоступление)."""
        return _q(self.fact_amount - self.plan_amount)

    @property
    def execution_pct(self) -> Decimal:
        """Процент выполнения (факт / план × 100). 0 если план = 0."""
        if self.plan_amount == ZERO:
            return ZERO
        return _q((self.fact_amount / self.plan_amount) * Decimal("100"))

    @property
    def has_gap(self) -> bool:
        """
        Признак проблемы:
        - для EXPENSE: факт > план (перерасход)
        - для INCOME:  факт < план (недопоступление)
        """
        if self.category_type == "expense":
            return self.fact_amount > self.plan_amount and self.plan_amount > ZERO
        return self.fact_amount < self.plan_amount and self.plan_amount > ZERO


@dataclass
class PlanFactReport:
    """Полный отчёт Плана-Факта за месяц."""
    company_id: UUID
    year:       int
    month:      int
    project_id: Optional[UUID]

    rows: list[CategoryPlanFact] = field(default_factory=list)

    @property
    def total_income_plan(self) -> Decimal:
        return _q(sum((r.plan_amount for r in self.rows if r.category_type == "income"), ZERO))

    @property
    def total_income_fact(self) -> Decimal:
        return _q(sum((r.fact_amount for r in self.rows if r.category_type == "income"), ZERO))

    @property
    def total_expense_plan(self) -> Decimal:
        return _q(sum((r.plan_amount for r in self.rows if r.category_type == "expense"), ZERO))

    @property
    def total_expense_fact(self) -> Decimal:
        return _q(sum((r.fact_amount for r in self.rows if r.category_type == "expense"), ZERO))

    @property
    def net_plan(self) -> Decimal:
        return _q(self.total_income_plan - self.total_expense_plan)

    @property
    def net_fact(self) -> Decimal:
        return _q(self.total_income_fact - self.total_expense_fact)


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = cal_module.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _build_depth_map(categories: list[Category]) -> dict[UUID, int]:
    """
    Вычисляет глубину каждой категории в дереве.
    Корневые категории (parent_id=None) имеют depth=0.
    """
    id_map = {c.id: c for c in categories}
    depth_cache: dict[UUID, int] = {}

    def _depth(cat_id: UUID) -> int:
        if cat_id in depth_cache:
            return depth_cache[cat_id]
        cat = id_map.get(cat_id)
        if cat is None or cat.parent_id is None:
            depth_cache[cat_id] = 0
        else:
            depth_cache[cat_id] = _depth(cat.parent_id) + 1
        return depth_cache[cat_id]

    for c in categories:
        _depth(c.id)
    return depth_cache


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────


async def get_plan_fact_report(
    db:         AsyncSession,
    company_id: UUID,
    year:       int,
    month:      int,
    project_id: Optional[UUID] = None,
) -> PlanFactReport:
    """
    Строит отчёт «План vs Факт» за один месяц для компании.

    Аргументы:
        db         — AsyncSession SQLAlchemy
        company_id — UUID компании
        year       — год (например, 2025)
        month      — месяц (1–12)
        project_id — опциональный фильтр по проекту;
                     None = показать общий бюджет без проекта

    Возвращает PlanFactReport со списком строк CategoryPlanFact,
    отсортированных: сначала родитель, затем его дети (pre-order DFS).
    """
    date_from, date_to = _month_bounds(year, month)

    # ── 1. Загружаем дерево категорий ──────────────────────────────────────
    cats_result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id == company_id,
                Category.is_deleted.is_(False),
            )
        ).order_by(Category.sort_order, Category.name)
    )
    all_categories: list[Category] = list(cats_result.scalars().all())

    if not all_categories:
        return PlanFactReport(
            company_id=company_id, year=year, month=month,
            project_id=project_id, rows=[],
        )

    depth_map = _build_depth_map(all_categories)

    # ── 2. Загружаем бюджеты за месяц ──────────────────────────────────────
    budget_filters = [
        Budget.company_id == company_id,
        Budget.year  == year,
        Budget.month == month,
    ]
    if project_id is not None:
        budget_filters.append(Budget.project_id == project_id)
    else:
        # Бюджет без привязки к проекту
        budget_filters.append(Budget.project_id.is_(None))

    budgets_result = await db.execute(
        select(Budget.category_id, Budget.plan_amount).where(and_(*budget_filters))
    )
    # category_id → plan_amount
    plan_map: dict[UUID, Decimal] = {
        row.category_id: row.plan_amount
        for row in budgets_result.all()
    }

    # ── 3. Агрегируем факт из транзакций по accrual_date ───────────────────
    fact_filters = [
        Transaction.company_id == company_id,
        Transaction.is_deleted.is_(False),
        Transaction.status    == TransactionStatus.CONFIRMED,
        Transaction.accrual_date >= date_from,
        Transaction.accrual_date <= date_to,
        Transaction.accrual_date.isnot(None),
        # Переводы между счетами не учитываются в P&L
        Transaction.transaction_type != TransactionType.TRANSFER,
    ]
    if project_id is not None:
        fact_filters.append(Transaction.project_id == project_id)

    fact_result = await db.execute(
        select(
            Transaction.category_id,
            Transaction.transaction_type,
            func.sum(Transaction.amount_base_currency).label("total"),
        )
        .where(and_(*fact_filters))
        .group_by(Transaction.category_id, Transaction.transaction_type)
    )

    # category_id → {income: Decimal, expense: Decimal}
    fact_map: dict[UUID | None, dict[str, Decimal]] = {}
    for row in fact_result.all():
        cid = row.category_id  # может быть None (категория не назначена)
        if cid not in fact_map:
            fact_map[cid] = {"income": ZERO, "expense": ZERO}
        amount = Decimal(str(row.total or "0"))
        if row.transaction_type == TransactionType.INCOME:
            fact_map[cid]["income"] += amount
        elif row.transaction_type == TransactionType.EXPENSE:
            fact_map[cid]["expense"] += amount

    # ── 4. Собираем строки отчёта ──────────────────────────────────────────
    cat_id_set = {c.id for c in all_categories}

    def _make_row(cat: Category) -> CategoryPlanFact:
        ctype  = cat.category_type.value  # "income" | "expense"
        plan   = plan_map.get(cat.id, ZERO)
        fact_d = fact_map.get(cat.id, {})
        fact   = fact_d.get(ctype, ZERO)
        return CategoryPlanFact(
            category_id   = cat.id,
            category_name = cat.name,
            category_type = ctype,
            parent_id     = cat.parent_id if cat.parent_id in cat_id_set else None,
            depth         = depth_map.get(cat.id, 0),
            plan_amount   = _q(plan),
            fact_amount   = _q(fact),
        )

    # Pre-order DFS: сначала корни, затем их дети по нарастающей глубине
    children_map: dict[Optional[UUID], list[Category]] = {}
    for cat in all_categories:
        key = cat.parent_id if (cat.parent_id and cat.parent_id in cat_id_set) else None
        children_map.setdefault(key, []).append(cat)

    ordered_rows: list[CategoryPlanFact] = []

    def _dfs(parent_id: Optional[UUID]) -> None:
        for cat in children_map.get(parent_id, []):
            ordered_rows.append(_make_row(cat))
            _dfs(cat.id)

    _dfs(None)

    return PlanFactReport(
        company_id=company_id,
        year=year,
        month=month,
        project_id=project_id,
        rows=ordered_rows,
    )
