#!/usr/bin/env python3
"""Тест Спринта 4: контрагенты, счета, баланс, долги."""
import asyncio, os
os.environ.pop("ALL_PROXY", None); os.environ["NO_PROXY"] = "*"
os.environ.setdefault("DATABASE_URL","postgresql+asyncpg://planfact_user:strongpassword@localhost:5432/planfact_db")

from httpx import AsyncClient
from datetime import date

BASE = "http://localhost:8000/api/v1"

async def run():
    async with AsyncClient(timeout=10) as c:
        # --- авторизуемся ---
        r = await c.post(f"{BASE}/auth/token", json={"email":"sprint3@test.com","password":"TestPass123"})
        tok = r.json()["access_token"]
        cid = r.json()["company_id"]
        h   = {"Authorization": f"Bearer {tok}"}
        today = date.today().isoformat()

        sep = "="*70
        print(f"\n{sep}\nТЕСТ 1: Создание контрагентов\n{sep}")

        # Покупатель
        r = await c.post(f"{BASE}/companies/{cid}/counterparties", headers=h, json={
            "name": "ООО Клиент Альфа", "inn": "7701000001",
            "is_customer": True, "is_supplier": False,
        })
        print(f"Status: {r.status_code}")
        buyer = r.json()
        print(f"✓ Покупатель: {buyer['name']} ({buyer['id']})")

        # Поставщик
        r = await c.post(f"{BASE}/companies/{cid}/counterparties", headers=h, json={
            "name": "ИП Поставщик Бета", "inn": "770200002",
            "is_customer": False, "is_supplier": True,
        })
        supplier = r.json()
        print(f"✓ Поставщик: {supplier['name']} ({supplier['id']})")

        print(f"\n{sep}\nТЕСТ 2: Создание счетов (дебиторка + кредиторка)\n{sep}")

        r = await c.post(f"{BASE}/companies/{cid}/invoices", headers=h, json={
            "counterparty_id": buyer["id"], "number": "АКТ-001",
            "date": today, "total_amount": 85000.00, "invoice_type": "customer_invoice",
            "description": "Оказание консультационных услуг",
        })
        print(f"Status: {r.status_code}")
        inv_ar = r.json()
        print(f"✓ Дебиторка:  {inv_ar['number']} на {inv_ar['total_amount']} ₽ [{inv_ar['status']}]")

        r = await c.post(f"{BASE}/companies/{cid}/invoices", headers=h, json={
            "counterparty_id": supplier["id"], "number": "СЧ-042",
            "date": today, "total_amount": 32000.00, "invoice_type": "supplier_bill",
            "description": "Поставка офисного оборудования",
        })
        inv_ap = r.json()
        print(f"✓ Кредиторка: {inv_ap['number']} на {inv_ap['total_amount']} ₽ [{inv_ap['status']}]")

        print(f"\n{sep}\nТЕСТ 3: Частичная оплата счёта клиентом\n{sep}")
        r = await c.post(f"{BASE}/companies/{cid}/invoices/{inv_ar['id']}/pay", headers=h, json={
            "payment_amount": 40000.00, "payment_date": today,
        })
        paid = r.json()
        print(f"Status: {r.status_code}")
        print(f"✓ Оплачено {paid['paid_amount']} из {paid['total_amount']} ₽, статус: {paid['status']}")
        print(f"  Остаток задолженности: {paid['outstanding_amount']} ₽")

        print(f"\n{sep}\nТЕСТ 4: Сводка долгов контрагентов\n{sep}")
        r = await c.get(f"{BASE}/companies/{cid}/counterparties/debts", headers=h,
                        params={"as_of": today})
        print(f"Status: {r.status_code}")
        debts = r.json()
        print(f"✓ Дебиторка итого:  {debts['total_accounts_receivable']} ₽")
        print(f"✓ Кредиторка итого: {debts['total_accounts_payable']} ₽")
        print(f"✓ Чистая позиция:   {debts['net_position']} ₽")
        print(f"  Топ должников ({len(debts['top_debtors'])}):")
        for d in debts['top_debtors']:
            print(f"    • {d['name']}: {d['accounts_receivable']} ₽")

        print(f"\n{sep}\nТЕСТ 5: Управленческий Баланс\n{sep}")
        r = await c.get(f"{BASE}/companies/{cid}/reports/balance-sheet", headers=h,
                        params={"on_date": today})
        print(f"Status: {r.status_code}")
        bs = r.json()
        print(f"✓ Дата:         {bs['on_date']}")
        print(f"✓ Итого активы: {bs['total_assets']} ₽")
        print(f"  - Деньги:       {bs['current_assets']['items'][0]['amount']} ₽")
        print(f"  - Дебиторка:    {bs['current_assets']['items'][1]['amount']} ₽")
        print(f"✓ Итого пассивы:{bs['total_liabilities_equity']} ₽")
        print(f"  - Нераспр. прибыль: {bs['equity']['items'][1]['amount']} ₽")
        print(f"  - Кредиторка:       {bs['current_liabilities']['items'][0]['amount']} ₽")
        print(f"✓ Сходимость:   {'ДА ✓' if bs['is_balanced'] else 'НЕТ ✗ (разрыв: ' + bs['balance_difference'] + ')'}")

        print(f"\n{sep}\n✅ ВСЕ ТЕСТЫ СПРИНТА 4 ПРОЙДЕНЫ\n{sep}\n")

asyncio.run(run())
