"""Seed script - добавляет тестовые данные в БД."""
import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.domain.models.finance import Transaction, TransactionType, TransactionStatus, Category, Account, AccountType
from app.domain.models.projects import Budget

DATABASE_URL = "postgresql+asyncpg://planfact_user:strongpassword@localhost:5432/planfact_db"

async def seed_test_data():
    """Add test data to database."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    company_id = UUID('a49eff66-4d42-406b-9c41-0094fe1ecadc')

    async with async_session() as session:
        try:
            # Transactions
            transactions = [
                Transaction(company_id=company_id, type=TransactionType.INCOME, amount=Decimal('500000'), description='Продажа товаров', date=date(2026, 6, 1), status=TransactionStatus.CONFIRMED),
                Transaction(company_id=company_id, type=TransactionType.EXPENSE, amount=Decimal('150000'), description='Зарплата сотрудникам', date=date(2026, 6, 2), status=TransactionStatus.CONFIRMED),
                Transaction(company_id=company_id, type=TransactionType.INCOME, amount=Decimal('250000'), description='Услуги консультирования', date=date(2026, 6, 5), status=TransactionStatus.CONFIRMED),
                Transaction(company_id=company_id, type=TransactionType.EXPENSE, amount=Decimal('50000'), description='Аренда офиса', date=date(2026, 6, 3), status=TransactionStatus.CONFIRMED),
                Transaction(company_id=company_id, type=TransactionType.EXPENSE, amount=Decimal('30000'), description='Интернет и связь', date=date(2026, 6, 4), status=TransactionStatus.CONFIRMED),
            ]
            for tx in transactions:
                session.add(tx)
            await session.flush()
            print(f"Added {len(transactions)} transactions")

            # Categories
            cat_revenue = Category(id=uuid4(), company_id=company_id, name='Доходы', code='4000', type='INCOME', is_system=True)
            cat_expense = Category(id=uuid4(), company_id=company_id, name='Расходы', code='5000', type='EXPENSE', is_system=True)
            session.add(cat_revenue)
            session.add(cat_expense)
            await session.flush()
            print("Added categories")

            # Budgets
            budgets = [
                Budget(company_id=company_id, category_id=cat_revenue.id, year=2026, month=6, plan_amount=Decimal('1000000')),
                Budget(company_id=company_id, category_id=cat_expense.id, year=2026, month=6, plan_amount=Decimal('400000')),
            ]
            for budget in budgets:
                session.add(budget)
            await session.flush()
            print(f"Added {len(budgets)} budgets")

            # Accounts
            accounts = [
                Account(id=uuid4(), company_id=company_id, code='1010', name='Касса', type=AccountType.ASSET, is_system=True, is_active=True),
                Account(id=uuid4(), company_id=company_id, code='5000', name='Доходы', type=AccountType.REVENUE, is_system=True, is_active=True),
            ]
            for acc in accounts:
                session.add(acc)
            await session.flush()
            print(f"Added {len(accounts)} accounts")

            await session.commit()
            print("Test data seeded successfully!")

        except Exception as e:
            await session.rollback()
            print(f"Error: {e}")
            raise
        finally:
            await engine.dispose()

if __name__ == '__main__':
    asyncio.run(seed_test_data())
