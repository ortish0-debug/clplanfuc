"""
Загрузка демо-данных через API для demo@example.com
"""
import asyncio
from datetime import date, timedelta
import httpx

BASE = "http://localhost:8000/api/v1"
EMAIL = "demo@example.com"
PASSWORD = "demo12345"


async def main():
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        # Авторизация
        r = await c.post(f"{BASE}/auth/token", data={"username": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        token = r.json()["access_token"]
        cid = r.json()["company_id"]
        h = {"Authorization": f"Bearer {token}"}
        print(f"✓ Авторизован. Company: {cid}")

        # ── СЧЕТА ─────────────────────────────────────────────
        accounts = [
            {"name": "Расчётный счёт (Сбербанк)", "account_type": "CHECKING", "currency": "RUB"},
            {"name": "Касса",                      "account_type": "CASH",     "currency": "RUB"},
            {"name": "Карточный счёт",             "account_type": "CHECKING", "currency": "RUB"},
        ]
        acc_ids = []
        for a in accounts:
            r = await c.post(f"{BASE}/companies/{cid}/accounts", json=a, headers=h)
            if r.status_code in (200, 201):
                acc_ids.append(r.json()["id"])
                print(f"  ✓ Счёт: {a['name']}")
            else:
                print(f"  ! Счёт '{a['name']}': {r.status_code} {r.text[:120]}")

        if not acc_ids:
            r = await c.get(f"{BASE}/companies/{cid}/accounts", headers=h)
            data = r.json()
            items = data.get("value", data) if isinstance(data, dict) else data
            acc_ids = [x["id"] for x in items if isinstance(x, dict)]

        print(f"  Счетов доступно: {len(acc_ids)}")

        # ── КАТЕГОРИИ ─────────────────────────────────────────
        cats_income  = ["Продажи", "Услуги", "Прочие доходы"]
        cats_expense = ["Зарплата", "Аренда", "Маркетинг", "IT и связь", "Налоги", "Прочие расходы"]

        inc_ids, exp_ids = [], []
        for name in cats_income:
            r = await c.post(f"{BASE}/companies/{cid}/categories",
                             json={"name": name, "category_type": "INCOME"}, headers=h)
            if r.status_code in (200, 201):
                inc_ids.append(r.json()["id"])
                print(f"  ✓ Категория (доход): {name}")
            else:
                print(f"  ! Категория '{name}': {r.status_code} {r.text[:80]}")

        for name in cats_expense:
            r = await c.post(f"{BASE}/companies/{cid}/categories",
                             json={"name": name, "category_type": "EXPENSE"}, headers=h)
            if r.status_code in (200, 201):
                exp_ids.append(r.json()["id"])
                print(f"  ✓ Категория (расход): {name}")
            else:
                print(f"  ! Категория '{name}': {r.status_code} {r.text[:80]}")

        if not inc_ids or not exp_ids:
            r = await c.get(f"{BASE}/companies/{cid}/categories", headers=h)
            data = r.json()
            items = data.get("value", data) if isinstance(data, dict) else data
            for x in items:
                if not isinstance(x, dict): continue
                t = x.get("category_type", "").upper()
                if t == "INCOME" and x["id"] not in inc_ids:   inc_ids.append(x["id"])
                if t == "EXPENSE" and x["id"] not in exp_ids:  exp_ids.append(x["id"])

        print(f"  Категорий доход: {len(inc_ids)}, расход: {len(exp_ids)}")

        if not acc_ids or not inc_ids or not exp_ids:
            print("  ОШИБКА: нет счетов или категорий, транзакции не создать")
            return

        today = date.today()
        aid  = acc_ids[0]
        iid  = inc_ids[0]
        eid  = exp_ids[0]

        # ── ТРАНЗАКЦИИ ────────────────────────────────────────
        txs = [
            # (days_ago, type, amount, description, cat_id)
            (90, "INCOME",  850_000, "Продажа товаров клиенту А",     iid),
            (85, "INCOME",  320_000, "Услуги консультирования",        inc_ids[1] if len(inc_ids)>1 else iid),
            (75, "INCOME",  540_000, "Продажа товаров клиенту Б",     iid),
            (70, "INCOME",  180_000, "Абонентская плата",              inc_ids[1] if len(inc_ids)>1 else iid),
            (60, "INCOME",  275_000, "Продажа ПО",                    iid),
            (55, "INCOME",  490_000, "Услуги разработки",             inc_ids[1] if len(inc_ids)>1 else iid),
            (45, "INCOME",  720_000, "Продажа товаров клиенту В",     iid),
            (30, "INCOME",  180_000, "Абонентская плата",              iid),
            (15, "INCOME",  210_000, "Услуги сопровождения",          inc_ids[1] if len(inc_ids)>1 else iid),
            (5,  "INCOME",  450_000, "Поступление от клиента",        iid),

            (88, "EXPENSE", 650_000, "Зарплата (август)",             eid),
            (83, "EXPENSE", 120_000, "Аренда офиса август",           exp_ids[1] if len(exp_ids)>1 else eid),
            (80, "EXPENSE",  85_000, "Google Ads",                    exp_ids[2] if len(exp_ids)>2 else eid),
            (78, "EXPENSE",  45_000, "Хостинг и серверы",             exp_ids[3] if len(exp_ids)>3 else eid),
            (65, "EXPENSE", 650_000, "Зарплата (сентябрь)",           eid),
            (60, "EXPENSE", 120_000, "Аренда офиса сентябрь",         exp_ids[1] if len(exp_ids)>1 else eid),
            (55, "EXPENSE", 120_000, "Реклама в соцсетях",            exp_ids[2] if len(exp_ids)>2 else eid),
            (42, "EXPENSE",  95_000, "Налог на прибыль",              exp_ids[4] if len(exp_ids)>4 else eid),
            (30, "EXPENSE",  28_000, "Офисные расходы",               eid),
            (28, "EXPENSE", 650_000, "Зарплата (октябрь)",            eid),
            (23, "EXPENSE", 120_000, "Аренда офиса октябрь",          exp_ids[1] if len(exp_ids)>1 else eid),
            (18, "EXPENSE",  12_000, "Интернет",                      exp_ids[3] if len(exp_ids)>3 else eid),
            (8,  "EXPENSE", 112_000, "НДС к уплате",                  exp_ids[4] if len(exp_ids)>4 else eid),
            (3,  "EXPENSE",  22_000, "Расходные материалы",           eid),
        ]

        ok = 0
        for days_ago, ttype, amount, desc, cat_id in txs:
            payload = {
                "transaction_type": ttype,
                "amount": amount,
                "description": desc,
                "payment_date": str(today - timedelta(days=days_ago)),
                "account_id": aid,
                "category_id": cat_id,
                "status": "COMPLETED",
            }
            r = await c.post(f"{BASE}/companies/{cid}/transactions", json=payload, headers=h)
            if r.status_code in (200, 201):
                ok += 1
            else:
                print(f"  ! Транзакция '{desc}': {r.status_code} {r.text[:100]}")

        print(f"  ✓ Транзакций создано: {ok}/{len(txs)}")

        print("\n" + "═"*50)
        print("  ГОТОВО")
        print("═"*50)
        print(f"  Фронтенд : http://localhost:8000/static/index.html")
        print(f"  Логин    : {EMAIL}")
        print(f"  Пароль   : {PASSWORD}")

asyncio.run(main())
