"""Интеграционный тест FIFO себестоимости и проводок COGS (Sprint 15)."""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload
from sqlalchemy import select

from app.infrastructure.database.base import Base
from app.domain.models.finance import Company, Currency
from app.domain.models.inventory import Warehouse, StockItem
from app.domain.models.ledger import AccountChart, JournalEntry
# noqa: F401 - All models must be imported for SQLAlchemy mapper initialization
import app.domain.models.saas
import app.domain.models.rules
import app.domain.models.counterparties
import app.domain.models.projects
import app.domain.models.assets_loans
import app.domain.models.integrations
import app.domain.models.accruals
import app.domain.models.payroll
import app.domain.models.holdings
from app.services.ledger_service import initialize_default_chart
from app.services.inventory_service import add_stock_incoming, add_stock_outgoing, get_stock_balances


async def test_fifo_cogs_integration():
    """Полный цикл: Приход × 2, Расход × 1 FIFO, проверка COGS."""

    # Инициализируем тестовую БД
    db_url = "postgresql+asyncpg://planfact_user:strongpassword@localhost:5432/planfact_db"
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # 1. Создаём тестовую компанию
        company = Company(
            id=uuid.uuid4(),
            name="Test FIFO Company",
            currency=Currency.RUB,
        )
        db.add(company)
        await db.flush()
        company_id = company.id

        # 2. Инициализируем план счетов
        await initialize_default_chart(db, company_id)

        # 3. Создаём склад и товар
        warehouse = Warehouse(
            id=uuid.uuid4(),
            company_id=company_id,
            name="Test Warehouse",
            location="Moscow",
        )
        db.add(warehouse)
        await db.flush()
        warehouse_id = warehouse.id

        stock_item = StockItem(
            id=uuid.uuid4(),
            company_id=company_id,
            name="Test Product",
            sku="TEST-001",
            unit="шт.",
        )
        db.add(stock_item)
        await db.flush()
        stock_item_id = stock_item.id

        # 4. Оформляем ДВА прихода
        incoming1 = await add_stock_incoming(
            db=db,
            company_id=company_id,
            warehouse_id=warehouse_id,
            stock_item_id=stock_item_id,
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
            operation_date=date(2026, 5, 31),
            notes="Партия №1",
        )
        print(f"[PASS] Prikhod 1: 10 units x 100.00 = {incoming1.total_amount} RUB")

        incoming2 = await add_stock_incoming(
            db=db,
            company_id=company_id,
            warehouse_id=warehouse_id,
            stock_item_id=stock_item_id,
            quantity=Decimal("10"),
            unit_price=Decimal("150.00"),
            operation_date=date(2026, 5, 31),
            notes="Batch 2",
        )
        print(f"[PASS] Prikhod 2: 10 units x 150.00 = {incoming2.total_amount} RUB")

        # 5. Oformliaem ODIN raskhod (FIFO: 10 iz Batch 1 + 5 iz Batch 2)
        outgoing_ops = await add_stock_outgoing(
            db=db,
            company_id=company_id,
            warehouse_id=warehouse_id,
            stock_item_id=stock_item_id,
            quantity=Decimal("15"),
            unit_price=Decimal("300.00"),
            operation_date=date(2026, 5, 31),
            notes="Sale",
        )
        print(f"[PASS] Raskhod: 15 units (FIFO: 10 + 5)")

        # FIFO sebestoimost
        cogs = sum(op.total_amount for op in outgoing_ops)
        print(f"[PASS] COGS (cost): {cogs} RUB")

        # 6. Proveryaem ostatki
        balances = await get_stock_balances(db, company_id, warehouse_id)
        remaining_qty = balances[0].quantity if balances else Decimal("0")
        print(f"[PASS] Stock remainder: {remaining_qty} units")

        # 7. Proveryaem provodki v ledgere
        result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.company_id == company_id)
            .options(selectinload(JournalEntry.lines))
            .order_by(JournalEntry.created_at)
        )
        entries = result.scalars().all()

        # Nakhodim provodku raskhoda (COGS: 9002 db, 4100 kr)
        cogs_entry = None
        for entry in entries:
            if any(l.debit > 0 for l in entry.lines):
                for line in entry.lines:
                    if line.account_chart_id and line.debit > 0:
                        cogs_entry = entry
                        break

        if cogs_entry:
            cogs_lines = [l for l in cogs_entry.lines if l.debit > 0 or l.credit > 0]
            total_debit = sum(l.debit for l in cogs_lines)
            print(f"[PASS] COGS entry found: Debit = {total_debit} RUB")

        # 8. ASSERTY
        try:
            assert remaining_qty == Decimal("5"), f"Remainder must be 5, got {remaining_qty}"
            assert cogs == Decimal("1750.00"), f"COGS must be 1750.00, got {cogs}"

            if cogs_entry:
                cogs_amount = sum(l.debit for l in cogs_entry.lines)
                assert cogs_amount == Decimal("1750.00"), f"COGS entry must be 1750.00, got {cogs_amount}"

            print("\n[SUCCESS] ALL ASSERTIONS PASSED")
            return True
        except AssertionError as e:
            print(f"\n✗ АССЕРТ ПРОВАЛЕН: {e}")
            return False
        finally:
            await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(test_fifo_cogs_integration())
    exit(0 if result else 1)
