#!/usr/bin/env python3
"""
Тест функционала импорта банковских выписок (Спринт 2).
Проверяет: парсер 1С, правила авто-категоризации, загрузку и подтверждение импорта.
"""
import asyncio
import json
from pathlib import Path
from decimal import Decimal
from httpx import AsyncClient
import sys
import os

# Отключаем системный SOCKS proxy
os.environ.pop("ALL_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ["NO_PROXY"] = "*"

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://planfact_user:strongpassword@localhost:5432/planfact_db")

# UUID из БД
CID = "9466702a-0d2b-4498-a4e0-2428d4e6ba30"
UID = "ce563b67-f8a4-411c-8e7d-c0599564b3fc"
AID = "9549bede-ad2d-4d91-a4c1-6542b8df11a3"
CAT_RENT = "f04fff3f-fd63-4237-9db2-d059819aa6cb"

API_BASE = "http://localhost:8000/api/v1"
HEADERS = {"Authorization": f"Bearer {UID}"}

async def test():
    async with AsyncClient(timeout=30.0) as client:
        # ТЕСТ 1: Создание правила
        print("\n" + "="*70)
        print("ТЕСТ 1: Создание правила автокатегоризации")
        print("="*70)

        rule_data = {
            "name": "Аренда офиса",
            "field_to_match": "description",
            "match_type": "contains",
            "pattern": "аренда",
            "suggested_category_id": CAT_RENT,
            "priority": 100,
            "is_active": True,
        }

        r = await client.post(
            f"{API_BASE}/companies/{CID}/import/rules",
            headers=HEADERS,
            json=rule_data,
        )
        print(f"Status: {r.status_code}")
        if r.status_code == 201:
            rule1 = r.json()
            print(f"✓ Правило создано: {rule1['name']}")
            print(f"  ID: {rule1['id']}")
            print(f"  Паттерн: '{rule1['pattern']}' в '{rule1['field_to_match']}'")
        else:
            print(f"✗ Ошибка: {r.text}")
            return

        # ТЕСТ 2: Загрузка файла для предпросмотра
        print("\n" + "="*70)
        print("ТЕСТ 2: Предпросмотр импорта выписки (POST /import/1c)")
        print("="*70)

        with open("test_statement.txt", "rb") as f:
            files = {"file": ("test.txt", f, "text/plain")}
            r = await client.post(
                f"{API_BASE}/companies/{CID}/import/1c",
                headers=HEADERS,
                files=files,
            )

        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            preview = r.json()
            print(f"✓ Файл разобран успешно")
            print(f"  Счёт: {preview['our_account']}")
            print(f"  Период: {preview['period_from']} .. {preview['period_to']}")
            print(f"  Транзакций: {preview['total_count']} (приход {preview['income_count']}, расход {preview['expense_count']})")
            print(f"  Авто-категоризировано правилами: {preview['rules_applied']}")
            print(f"\n  Сумма приходов: {preview['total_income']} ₽")
            print(f"  Сумма расходов: {preview['total_expense']} ₽")

            print(f"\n  Распознанные транзакции:")
            for i, t in enumerate(preview['transactions'], 1):
                cat_info = f" → {t['suggested_category_name']}" if t['suggested_category_name'] else ""
                rule_info = f" (правило: {t['rule_name']})" if t['rule_name'] else ""
                print(f"    {i}. {t['payment_date']} | {t['transaction_type']:7} | {t['amount']:>10} | {t['counterparty_name'][:30]:30} | {t['description'][:40]:40}{cat_info}{rule_info}")

            txns_for_confirm = preview['transactions']
        else:
            print(f"✗ Ошибка: {r.text}")
            return

        # ТЕСТ 3: Подтверждение импорта
        print("\n" + "="*70)
        print("ТЕСТ 3: Подтверждение импорта (POST /import/1c/confirm)")
        print("="*70)

        confirm_txns = [
            {
                "doc_number": t.get("doc_number"),
                "payment_date": t["payment_date"],
                "accrual_date": t["payment_date"],
                "amount": float(t["amount"]),  # Преобразуем Decimal в float
                "transaction_type": t["transaction_type"],
                "counterparty": t.get("counterparty_name"),
                "counterparty_inn": t.get("counterparty_inn"),
                "description": t["description"],
                "category_id": t.get("suggested_category_id"),
            }
            for t in txns_for_confirm
        ]

        confirm_data = {
            "account_id": AID,
            "transactions": confirm_txns,
        }

        r = await client.post(
            f"{API_BASE}/companies/{CID}/import/1c/confirm",
            headers=HEADERS,
            json=confirm_data,
        )

        print(f"Status: {r.status_code}")
        if r.status_code == 201:
            result = r.json()
            print(f"✓ Импорт завершён")
            print(f"  Импортировано: {result['imported_count']} операций")
            print(f"  Пропущено дубликатов: {result['skipped_count']}")
            print(f"  Поступления: {result['total_income']} ₽")
            print(f"  Расходы: {result['total_expense']} ₽")
        else:
            print(f"✗ Ошибка: {r.text}")
            return

        # ТЕСТ 4: Проверка в БД
        print("\n" + "="*70)
        print("ТЕСТ 4: Проверка транзакций в БД")
        print("="*70)

        # Используем БД напрямую
        import dotenv
        dotenv.load_dotenv()
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import select

        engine = create_async_engine(os.environ["DATABASE_URL"])
        SessionFactory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with SessionFactory() as db:
            from app.domain.models.finance import Transaction
            result = await db.execute(
                select(Transaction)
                .where(Transaction.account_id == AID)
                .order_by(Transaction.payment_date.desc())
                .limit(3)
            )
            txns = result.scalars().all()
            print(f"✓ В БД найдено транзакций (последние 3):")
            for t in txns:
                print(f"  • {t.payment_date} | {t.transaction_type.value:7} | {t.amount:>10} ₽ | {t.description[:50]}")

        await engine.dispose()

        print("\n" + "="*70)
        print("✅ Все тесты прошли успешно!")
        print("="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(test())
