"""
Временный скрипт начального наполнения БД тестовыми данными.
Запуск: python init_db.py

Создаёт:
  1. Тестового пользователя (owner@planfact.dev / Test1234!)
  2. Компанию ООО «ТестКомпания»
  3. Роль OWNER для пользователя в этой компании
  4. Тарифный план FREE с Feature Flags
  5. Подписку компании на FREE-план
  6. Три счёта: расчётный, кассовый, карточный
  7. Базовые категории доходов и расходов

После запуска в консоль выводятся UUID, которые нужно вставить
в настройки фронтенда (Company ID, Bearer Token).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# Загружаем .env до импорта модулей приложения
import dotenv
dotenv.load_dotenv()

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import (
    Account,
    AccountType,
    Category,
    CategoryType,
    Company,
    Currency,
)
from app.domain.models.saas import (
    BillingInterval,
    Subscription,
    SubscriptionPlan,
    SubscriptionStatus,
    User,
    UserCompanyRole,
    UserRole,
)
from app.infrastructure.database.session import AsyncSessionFactory


# ---------------------------------------------------------------------------
# Утилита: безопасный bcrypt-хэш через passlib
# ---------------------------------------------------------------------------

def _hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


# ---------------------------------------------------------------------------
# Вспомогательная функция: проверяем, не создан ли объект уже
# ---------------------------------------------------------------------------

async def _exists(session: AsyncSession, model, **filters) -> bool:
    conditions = [getattr(model, k) == v for k, v in filters.items()]
    result = await session.execute(select(model).where(*conditions))
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

async def init() -> None:
    print("\n" + "═" * 58)
    print("  PlanFact — инициализация тестовых данных")
    print("═" * 58)

    async with AsyncSessionFactory() as session:
        # ── 1. Тарифный план FREE ────────────────────────────────
        plan_slug = "free"
        if not await _exists(session, SubscriptionPlan, slug=plan_slug):
            plan = SubscriptionPlan(
                id=uuid.uuid4(),
                slug=plan_slug,
                name="Бесплатный",
                description="Базовый план для старта",
                price_monthly_cents=0,
                price_quarterly_cents=0,
                price_annual_cents=0,
                currency_code="RUB",
                feature_flags={
                    "can_use_ai":            False,
                    "max_accounts":          3,
                    "max_users":             2,
                    "can_sync_banks":        False,
                    "can_export_xlsx":       False,
                    "can_use_multi_currency":False,
                    "can_use_budgets":       False,
                    "support_priority":      "community",
                },
                is_public=True,
                is_active=True,
                sort_order=0,
            )
            session.add(plan)
            await session.flush()
            print(f"  ✓ Тарифный план   : {plan.name} (slug={plan.slug})")
        else:
            result = await session.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.slug == plan_slug)
            )
            plan = result.scalar_one()
            print(f"  · Тарифный план   : уже существует ({plan.slug})")

        # ── 2. Тестовый пользователь ─────────────────────────────
        test_email = "owner@planfact.dev"
        if not await _exists(session, User, email=test_email):
            user = User(
                id=uuid.uuid4(),
                email=test_email,
                hashed_password=_hash_password("Test1234!"),
                full_name="Тестовый Владелец",
                is_active=True,
                is_verified=True,
                is_superuser=False,
                email_verified_at=datetime.now(tz=timezone.utc),
                locale="ru",
                timezone="Europe/Moscow",
            )
            session.add(user)
            await session.flush()
            print(f"  ✓ Пользователь    : {user.email}")
            print(f"    Пароль          : Test1234!")
            print(f"    Bearer Token    : {user.id}  ← скопируй в настройки")
        else:
            result = await session.execute(
                select(User).where(User.email == test_email)
            )
            user = result.scalar_one()
            print(f"  · Пользователь    : уже существует ({user.email})")
            print(f"    Bearer Token    : {user.id}  ← скопируй в настройки")

        # ── 3. Компания ──────────────────────────────────────────
        company_name = "ООО «ТестКомпания»"
        existing_companies = await session.execute(
            select(Company).where(Company.name == company_name)
        )
        existing_company = existing_companies.scalar_one_or_none()

        if not existing_company:
            company = Company(
                id=uuid.uuid4(),
                name=company_name,
                legal_name="Общество с ограниченной ответственностью «ТестКомпания»",
                inn="7700000000",
                currency=Currency.RUB,
                timezone="Europe/Moscow",
                settings={
                    "tax_regime":     "usn_income",
                    "business_model": "standard",
                    "currency":       "RUB",
                },
                is_deleted=False,
            )
            session.add(company)
            await session.flush()
            print(f"  ✓ Компания        : {company.name}")
            print(f"    Company ID      : {company.id}  ← скопируй в настройки")
        else:
            company = existing_company
            print(f"  · Компания        : уже существует ({company.name})")
            print(f"    Company ID      : {company.id}  ← скопируй в настройки")

        # ── 4. Роль пользователя в компании ─────────────────────
        if not await _exists(session, UserCompanyRole,
                              user_id=user.id, company_id=company.id):
            role = UserCompanyRole(
                id=uuid.uuid4(),
                user_id=user.id,
                company_id=company.id,
                role=UserRole.OWNER,
                is_active=True,
                custom_permissions={},
                joined_at=datetime.now(tz=timezone.utc),
            )
            session.add(role)
            await session.flush()
            print(f"  ✓ Роль            : {role.role.value} в компании {company.name}")
        else:
            print(f"  · Роль            : уже существует (OWNER)")

        # ── 5. Подписка ──────────────────────────────────────────
        existing_sub = await session.execute(
            select(Subscription).where(
                Subscription.company_id == company.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        if not existing_sub.scalar_one_or_none():
            sub = Subscription(
                id=uuid.uuid4(),
                company_id=company.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                billing_interval=BillingInterval.MONTHLY,
                feature_flags_snapshot=plan.feature_flags,
            )
            session.add(sub)
            await session.flush()
            print(f"  ✓ Подписка        : FREE (active)")
        else:
            print(f"  · Подписка        : уже существует")

        # ── 6. Счета компании ────────────────────────────────────
        accounts_data = [
            dict(
                name="Расчётный счёт (Сбербанк)",
                account_type=AccountType.CHECKING,
                initial_balance=Decimal("250000.00"),
                current_balance=Decimal("250000.00"),
                bank_name="Сбербанк",
                color="#22c55e",
            ),
            dict(
                name="Касса",
                account_type=AccountType.CASH,
                initial_balance=Decimal("15000.00"),
                current_balance=Decimal("15000.00"),
                bank_name=None,
                color="#f59e0b",
            ),
            dict(
                name="Корпоративная карта",
                account_type=AccountType.CHECKING,
                initial_balance=Decimal("30000.00"),
                current_balance=Decimal("30000.00"),
                bank_name="Тинькофф",
                color="#6366f1",
            ),
        ]

        created_accounts: list[Account] = []
        for i, acc_data in enumerate(accounts_data):
            existing_acc = await session.execute(
                select(Account).where(
                    Account.company_id == company.id,
                    Account.name == acc_data["name"],
                )
            )
            if not existing_acc.scalar_one_or_none():
                acc = Account(
                    id=uuid.uuid4(),
                    company_id=company.id,
                    currency=Currency.RUB,
                    sort_order=i,
                    is_active=True,
                    is_deleted=False,
                    **acc_data,
                )
                session.add(acc)
                created_accounts.append(acc)
        await session.flush()
        if created_accounts:
            for acc in created_accounts:
                print(f"  ✓ Счёт            : {acc.name} ({acc.current_balance} ₽)")
                print(f"    Account ID      : {acc.id}  ← используй в ИИ-парсере")
        else:
            existing_accs = await session.execute(
                select(Account).where(Account.company_id == company.id)
            )
            for acc in existing_accs.scalars().all():
                print(f"  · Счёт            : уже существует ({acc.name})")
                print(f"    Account ID      : {acc.id}  ← используй в ИИ-парсере")

        # ── 7. Категории ─────────────────────────────────────────
        categories_data = [
            # Доходы
            dict(name="Выручка от продаж",    ct=CategoryType.INCOME,  icon="sales_revenue",    color="#22c55e"),
            dict(name="Прочие доходы",        ct=CategoryType.INCOME,  icon="other_income",     color="#86efac"),
            dict(name="Возвраты",             ct=CategoryType.INCOME,  icon="refund",           color="#bbf7d0"),
            # Расходы: себестоимость
            dict(name="Себестоимость (COGS)", ct=CategoryType.EXPENSE, icon="cogs",             color="#ef4444"),
            dict(name="Зарплата (ФОТ)",       ct=CategoryType.EXPENSE, icon="payroll",          color="#f97316"),
            dict(name="Аренда",               ct=CategoryType.EXPENSE, icon="rent",             color="#f59e0b"),
            dict(name="Маркетинг и реклама",  ct=CategoryType.EXPENSE, icon="marketing",        color="#a855f7"),
            dict(name="Налоги и взносы",      ct=CategoryType.EXPENSE, icon="taxes",            color="#ec4899"),
            dict(name="Банковские комиссии",  ct=CategoryType.EXPENSE, icon="bank_fees",        color="#64748b"),
            dict(name="Амортизация",          ct=CategoryType.EXPENSE, icon="depreciation",     color="#94a3b8"),
            dict(name="Проценты по кредитам", ct=CategoryType.EXPENSE, icon="interest",         color="#dc2626"),
            dict(name="Прочие расходы",       ct=CategoryType.EXPENSE, icon="other",            color="#cbd5e1"),
        ]

        new_cats = 0
        for cat_data in categories_data:
            exists = await session.execute(
                select(Category).where(
                    Category.company_id == company.id,
                    Category.icon == cat_data["icon"],
                )
            )
            if not exists.scalar_one_or_none():
                cat = Category(
                    id=uuid.uuid4(),
                    company_id=company.id,
                    name=cat_data["name"],
                    category_type=cat_data["ct"],
                    icon=cat_data["icon"],
                    color=cat_data["color"],
                    is_system=True,
                    is_deleted=False,
                )
                session.add(cat)
                new_cats += 1
        await session.flush()
        if new_cats:
            print(f"  ✓ Категории       : создано {new_cats} штук")
        else:
            print(f"  · Категории       : уже существуют")

        # ── Фиксируем всё одной транзакцией ─────────────────────
        await session.commit()

    # ── Итоговый вывод ───────────────────────────────────────────
    print()
    print("═" * 58)
    print("  ✅  База данных готова к работе!")
    print("═" * 58)
    print()
    print("  📋  Что вставить в настройки фронтенда:")
    print(f"      API URL     : http://localhost:8000")
    print(f"      Company ID  : {company.id}")
    print(f"      Token       : {user.id}")
    print()
    print("  🔑  Данные для входа (будущий auth):")
    print(f"      Email       : {user.email}")
    print(f"      Пароль      : Test1234!")
    print()
    print("  📌  Swagger UI  : http://localhost:8000/api/docs")
    print("  📌  Frontend    : http://localhost:8000/static/index.html")
    print("═" * 58 + "\n")


if __name__ == "__main__":
    asyncio.run(init())
