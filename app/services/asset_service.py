"""
Сервис: Учёт Основных Средств.

run_monthly_amortization() — ежемесячный запуск начисления амортизации:
  1. Выбирает все активные, не полностью самортизированные ОС компании.
  2. Для каждого ОС считает ежемесячную сумму (линейный метод).
  3. Увеличивает accumulated_amortization, но не выше purchase_cost.
  4. Создаёт EXPENSE-транзакцию с accrual_date = current_date
     и category.icon = "depreciation" (для корректного учёта в P&L).
  5. Возвращает список AmortizationResult.

Идемпотентность: если для ОС уже есть транзакция амортизации с тегом
  {"amortization_month": "YYYY-MM", "asset_id": "..."}
за текущий месяц, повторное начисление пропускается.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.assets_loans import Asset
from app.domain.models.finance import (
    Account,
    Category,
    CategoryType,
    Transaction,
    TransactionStatus,
    TransactionType,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

_AMORT_ICON  = "depreciation"          # icon категории амортизации в P&L-классификаторе
_AMORT_NAME  = "Амортизация (ОС)"     # имя системной категории
_TAG_KEY     = "amortization_month"   # тег идемпотентности в Transaction.tags


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗУЛЬТАТ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AmortizationResult:
    """Итог начисления амортизации по одному ОС за период."""
    asset_id:         UUID
    asset_name:       str
    month_label:      str        # "YYYY-MM"
    amount:           Decimal    # начисленная за месяц амортизация
    accumulated:      Decimal    # накопленная амортизация ПОСЛЕ начисления
    residual_value:   Decimal    # остаточная стоимость ПОСЛЕ начисления
    is_fully_amortized: bool
    transaction_id:   Optional[UUID]   # None если пропущено (идемпотент)
    skipped:          bool       # True — дубликат, начисление не производилось


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _get_or_create_depreciation_category(
    db:         AsyncSession,
    company_id: UUID,
) -> Category:
    """
    Находит или создаёт системную категорию «Амортизация (ОС)».
    Категория: тип EXPENSE, icon='depreciation' (читается P&L-калькулятором).
    """
    result = await db.execute(
        select(Category).where(
            and_(
                Category.company_id    == company_id,
                Category.icon          == _AMORT_ICON,
                Category.is_deleted.is_(False),
            )
        ).limit(1)
    )
    cat = result.scalar_one_or_none()
    if cat:
        return cat

    # Системная категория — нельзя удалить/переименовать через UI
    cat = Category(
        id=uuid.uuid4(),
        company_id=company_id,
        name=_AMORT_NAME,
        category_type=CategoryType.EXPENSE,
        icon=_AMORT_ICON,
        is_system=True,
        sort_order=9000,
        is_deleted=False,
    )
    db.add(cat)
    await db.flush()
    return cat


async def _get_primary_account(
    db:         AsyncSession,
    company_id: UUID,
    preferred_account_id: Optional[UUID] = None,
) -> Optional[Account]:
    """
    Возвращает счёт для проводки амортизации.
    Приоритет: preferred_account_id → первый активный счёт компании.
    """
    if preferred_account_id:
        result = await db.execute(
            select(Account).where(
                and_(
                    Account.id         == preferred_account_id,
                    Account.company_id == company_id,
                    Account.is_active.is_(True),
                    Account.is_deleted.is_(False),
                )
            )
        )
        acc = result.scalar_one_or_none()
        if acc:
            return acc

    result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def _has_amortization_this_month(
    db:         AsyncSession,
    company_id: UUID,
    asset_id:   UUID,
    month_label: str,
) -> bool:
    """
    Проверяет, была ли уже создана транзакция амортизации для данного ОС
    в указанном месяце (YYYY-MM). Используем JSONB-поиск по полю tags.
    """
    result = await db.execute(
        select(func.count()).select_from(Transaction).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.asset_id   == asset_id,
                Transaction.is_deleted.is_(False),
                Transaction.tags.contains([{_TAG_KEY: month_label}]),
            )
        )
    )
    count = result.scalar() or 0
    return count > 0


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────


async def run_monthly_amortization(
    db:           AsyncSession,
    company_id:   UUID,
    current_date: date,
) -> list[AmortizationResult]:
    """
    Начисляет ежемесячную амортизацию (износ) по линейному методу
    для всех активных ОС компании.

    Вызывается в начале каждого месяца (или вручную из PATCH /assets/amortize).

    Параметры:
        db           — AsyncSession SQLAlchemy
        company_id   — UUID компании
        current_date — дата начисления (обычно первый день текущего месяца)

    Возвращает список AmortizationResult (по одному на каждое ОС).
    Пропускает полностью самортизированные ОС и дубликаты (идемпотентность).
    """
    month_label = current_date.strftime("%Y-%m")

    # ── 1. Загружаем все активные, не удалённые ОС компании ────────────────
    assets_result = await db.execute(
        select(Asset).where(
            and_(
                Asset.company_id == company_id,
                Asset.is_active.is_(True),
                Asset.is_deleted.is_(False),
            )
        ).order_by(Asset.name)
    )
    assets = list(assets_result.scalars().all())

    if not assets:
        return []

    # ── 2. Подготавливаем общие ресурсы один раз ──────────────────────────
    category   = await _get_or_create_depreciation_category(db, company_id)
    account    = await _get_primary_account(db, company_id)
    results:   list[AmortizationResult] = []

    for asset in assets:
        # Пропускаем полностью самортизированные
        if asset.accumulated_amortization >= asset.purchase_cost:
            results.append(AmortizationResult(
                asset_id=asset.id,
                asset_name=asset.name,
                month_label=month_label,
                amount=ZERO,
                accumulated=asset.accumulated_amortization,
                residual_value=asset.residual_value,
                is_fully_amortized=True,
                transaction_id=None,
                skipped=True,
            ))
            continue

        # Идемпотентность: пропускаем дублирующее начисление
        if await _has_amortization_this_month(db, company_id, asset.id, month_label):
            results.append(AmortizationResult(
                asset_id=asset.id,
                asset_name=asset.name,
                month_label=month_label,
                amount=ZERO,
                accumulated=asset.accumulated_amortization,
                residual_value=asset.residual_value,
                is_fully_amortized=asset.is_fully_amortized,
                transaction_id=None,
                skipped=True,
            ))
            continue

        # ── Расчёт суммы амортизации ─────────────────────────────────────
        monthly_amount = asset.monthly_amortization

        # Не начислять больше, чем осталось
        max_possible = _q(asset.purchase_cost - asset.accumulated_amortization)
        amount = min(monthly_amount, max_possible)

        if amount <= ZERO:
            continue

        # ── Обновляем накопленную амортизацию ────────────────────────────
        asset.accumulated_amortization = _q(
            asset.accumulated_amortization + amount
        )

        # Если полностью самортизированы — деактивируем
        if asset.is_fully_amortized:
            asset.is_active = False

        # ── Создаём EXPENSE-транзакцию для P&L ───────────────────────────
        txn_id: Optional[UUID] = None

        if account is not None:
            txn = Transaction(
                id=uuid.uuid4(),
                company_id=company_id,
                account_id=account.id,
                category_id=category.id,
                asset_id=asset.id,                         # ← привязка к ОС
                transaction_type=TransactionType.EXPENSE,
                status=TransactionStatus.CONFIRMED,
                amount=amount,
                currency=account.currency,                 # валюта счёта
                exchange_rate=Decimal("1.000000"),
                amount_base_currency=amount,
                payment_date=current_date,                 # ДДС: деньги "потрачены"
                accrual_date=current_date,                 # P&L: метод начисления
                description=(
                    f"Амортизация: {asset.name} "
                    f"({month_label})"
                ),
                tags=[{_TAG_KEY: month_label, "asset_id": str(asset.id)}],
                meta={"auto_generated": True, "service": "asset_amortization"},
                ai_classified=False,
                is_deleted=False,
            )
            db.add(txn)
            txn_id = txn.id

        await db.flush()

        results.append(AmortizationResult(
            asset_id=asset.id,
            asset_name=asset.name,
            month_label=month_label,
            amount=amount,
            accumulated=asset.accumulated_amortization,
            residual_value=asset.residual_value,
            is_fully_amortized=asset.is_fully_amortized,
            transaction_id=txn_id,
            skipped=False,
        ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# СВОДКА ОС (для карточки)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AssetSummary:
    asset_id:                UUID
    name:                    str
    purchase_cost:           Decimal
    purchase_date:           date
    amortization_months:     int
    monthly_amortization:    Decimal
    accumulated_amortization: Decimal
    residual_value:          Decimal
    months_elapsed:          int      # сколько месяцев уже начислено
    is_fully_amortized:      bool


async def get_asset_summary(
    db:       AsyncSession,
    asset_id: UUID,
    company_id: UUID,
) -> AssetSummary:
    """Возвращает карточку ОС с вычисленными полями."""
    result = await db.execute(
        select(Asset).where(
            and_(Asset.id == asset_id, Asset.company_id == company_id)
        )
    )
    asset: Optional[Asset] = result.scalar_one_or_none()
    if asset is None:
        raise ValueError(f"ОС {asset_id} не найдено.")

    # Определяем количество начисленных месяцев из транзакций
    months_result = await db.execute(
        select(func.count()).select_from(Transaction).where(
            and_(
                Transaction.asset_id   == asset_id,
                Transaction.company_id == company_id,
                Transaction.is_deleted.is_(False),
                # только транзакции амортизации (с нашим тегом)
                Transaction.tags.contains([{_TAG_KEY: {"$exists": True}}]),
            )
        )
    )
    months_elapsed = months_result.scalar() or 0

    return AssetSummary(
        asset_id=asset.id,
        name=asset.name,
        purchase_cost=asset.purchase_cost,
        purchase_date=asset.purchase_date,
        amortization_months=asset.amortization_months,
        monthly_amortization=asset.monthly_amortization,
        accumulated_amortization=asset.accumulated_amortization,
        residual_value=asset.residual_value,
        months_elapsed=months_elapsed,
        is_fully_amortized=asset.is_fully_amortized,
    )
