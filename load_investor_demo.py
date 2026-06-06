"""
Axiom Finance — полные демо-данные для показа инвестору.
Запуск: python load_investor_demo.py
"""
import asyncio
import calendar as cal
import httpx
from datetime import date, timedelta

BASE = "http://localhost:8000/api/v1"
EMAIL = "demo@example.com"
PASSWORD = "demo12345"


async def post(c, url, data, h):
    r = await c.post(url, json=data, headers=h)
    if r.status_code in (200, 201):
        return r.json()
    print(f"    WARN {url.split('/')[-1]}: {r.status_code} {r.text[:120]}")
    return None


async def main():
    async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
        r = await c.post(f"{BASE}/auth/token", data={"username": EMAIL, "password": PASSWORD})
        d = r.json(); token = d["access_token"]; cid = d["company_id"]
        h = {"Authorization": f"Bearer {token}"}
        today = date.today()
        print(f"✓ Axiom Finance | company: {cid}\n")

        # ── СЧЕТА ─────────────────────────────────────────────
        print("── Счета...")
        accs_r = (await c.get(f"{BASE}/companies/{cid}/accounts", headers=h)).json()
        existing = accs_r if isinstance(accs_r, list) else accs_r.get("accounts", accs_r.get("value", []))
        existing_names = {a["name"] for a in existing}
        new_accs = [
            {"name": "Расчётный счёт (Сбербанк)", "account_type": "CHECKING", "currency": "RUB"},
            {"name": "Расчётный счёт (Тинькофф)", "account_type": "CHECKING", "currency": "RUB"},
            {"name": "Касса",                      "account_type": "CASH",     "currency": "RUB"},
            {"name": "Депозит",                    "account_type": "SAVINGS",  "currency": "RUB"},
        ]
        acc_ids = [a["id"] for a in existing]
        for a in new_accs:
            if a["name"] not in existing_names:
                res = await post(c, f"{BASE}/companies/{cid}/accounts", a, h)
                if res: acc_ids.append(res["id"])
        aid = acc_ids[0]
        print(f"  Счетов: {len(acc_ids)}")

        # ── КАТЕГОРИИ ─────────────────────────────────────────
        print("\n── Категории...")
        cats_r = (await c.get(f"{BASE}/companies/{cid}/categories", headers=h)).json()
        existing_cats = cats_r if isinstance(cats_r, list) else cats_r.get("categories", cats_r.get("value", []))
        existing_cat_names = {x["name"] for x in existing_cats}
        income_names  = ["Продажи B2B", "Продажи B2C", "Подписки и лицензии", "Консалтинг", "Прочие доходы"]
        expense_names = ["Зарплата и ФОТ", "Аренда", "Маркетинг и реклама", "IT-инфраструктура",
                         "Налоги и сборы", "Командировки", "Административные расходы"]
        iids, eids = [], []
        for nm in income_names:
            if nm in existing_cat_names:
                iids.append(next(x["id"] for x in existing_cats if x["name"] == nm))
            else:
                res = await post(c, f"{BASE}/companies/{cid}/categories", {"name": nm, "category_type": "INCOME"}, h)
                if res: iids.append(res["id"])
        for nm in expense_names:
            if nm in existing_cat_names:
                eids.append(next(x["id"] for x in existing_cats if x["name"] == nm))
            else:
                res = await post(c, f"{BASE}/companies/{cid}/categories", {"name": nm, "category_type": "EXPENSE"}, h)
                if res: eids.append(res["id"])
        # fallback — добавляем существующие
        for x in existing_cats:
            t = x.get("category_type", "").upper()
            if t == "INCOME"  and x["id"] not in iids: iids.append(x["id"])
            if t == "EXPENSE" and x["id"] not in eids: eids.append(x["id"])
        print(f"  Доходных: {len(iids)} | Расходных: {len(eids)}")

        # ── ТРАНЗАКЦИИ (6 месяцев, рост +15%/мес) ─────────────
        print("\n── Транзакции (6 мес., тренд роста)...")
        txs = []
        rev_base = 1_800_000
        exp_base = 1_400_000
        for m_ago in range(5, -1, -1):
            mo = today.month - m_ago
            yr = today.year
            while mo <= 0: mo += 12; yr -= 1
            rev = int(rev_base * (1.15 ** (5 - m_ago)))
            exp = int(exp_base * (1.08 ** (5 - m_ago)))
            last = cal.monthrange(yr, mo)[1]
            for day, pct, desc, cat in [
                (5,  0.35, "Продажи B2B — крупный клиент",  iids[0]),
                (10, 0.20, "Продажи B2C",                   iids[1] if len(iids)>1 else iids[0]),
                (15, 0.15, "Подписки и лицензии",           iids[2] if len(iids)>2 else iids[0]),
                (20, 0.18, "Продажи B2B — средний клиент",  iids[0]),
                (25, 0.12, "Консалтинг",                    iids[3] if len(iids)>3 else iids[0]),
            ]:
                txs.append({"transaction_type":"INCOME","amount":int(rev*pct),"description":desc,
                             "payment_date":f"{yr}-{mo:02d}-{min(day,last):02d}",
                             "account_id":aid,"category_id":cat,"status":"COMPLETED"})
            for day, pct, desc, cat in [
                (5,  0.42, "Зарплата и ФОТ",            eids[0]),
                (3,  0.10, "Аренда офиса",               eids[1] if len(eids)>1 else eids[0]),
                (12, 0.12, "Маркетинг и реклама",        eids[2] if len(eids)>2 else eids[0]),
                (15, 0.07, "IT-инфраструктура",           eids[3] if len(eids)>3 else eids[0]),
                (20, 0.09, "Налоги и сборы",             eids[4] if len(eids)>4 else eids[0]),
                (25, 0.05, "Командировки",               eids[5] if len(eids)>5 else eids[0]),
                (28, 0.15, "Административные расходы",   eids[6] if len(eids)>6 else eids[0]),
            ]:
                txs.append({"transaction_type":"EXPENSE","amount":int(exp*pct),"description":desc,
                             "payment_date":f"{yr}-{mo:02d}-{min(day,last):02d}",
                             "account_id":aid,"category_id":cat,"status":"COMPLETED"})
        ok = 0
        for tx in txs:
            r2 = await c.post(f"{BASE}/companies/{cid}/transactions", json=tx, headers=h)
            if r2.status_code in (200, 201): ok += 1
        print(f"  ✓ {ok}/{len(txs)}")

        # ── КОНТРАГЕНТЫ ───────────────────────────────────────
        print("\n── Контрагенты...")
        cps = [
            {"name":"ООО «Технологии Будущего»","inn":"7701234567","is_customer":True,"is_supplier":False,"contact_email":"info@techfuture.ru","contact_phone":"+7 495 123-45-67"},
            {"name":"АО «Мегасофт»",            "inn":"7709876543","is_customer":True,"is_supplier":False,"contact_email":"finance@megasoft.ru"},
            {"name":"ИП Романов Алексей",        "is_customer":True,"is_supplier":False,"contact_phone":"+7 916 234-56-78"},
            {"name":"ООО «ДатаПро»",             "inn":"7712345001","is_customer":True,"is_supplier":False},
            {"name":"ООО «Ситроникс»",           "inn":"7714567890","is_customer":True,"is_supplier":False},
            {"name":"ЗАО «Арендодатель Плюс»",  "inn":"7703456789","is_customer":False,"is_supplier":True},
            {"name":"ООО «Облачные Сервисы»",   "inn":"7705678901","is_customer":False,"is_supplier":True},
            {"name":"ИП Петрова Светлана",       "is_customer":False,"is_supplier":True,"contact_email":"petrova@design.ru"},
        ]
        cp_ok = 0
        for cp in cps:
            res = await post(c, f"{BASE}/companies/{cid}/counterparties", cp, h)
            if res: cp_ok += 1
        print(f"  ✓ {cp_ok}/{len(cps)}")

        # ── ПРОЕКТЫ ───────────────────────────────────────────
        print("\n── Проекты...")
        projects = [
            {"name":"Axiom Enterprise Platform", "description":"Разработка enterprise-версии",    "status":"ACTIVE",    "budget":8_500_000},
            {"name":"Mobile App (iOS + Android)", "description":"Мобильное приложение",           "status":"ACTIVE",    "budget":3_200_000},
            {"name":"1С интеграция",              "description":"Двусторонняя синхронизация с 1С","status":"ACTIVE",    "budget":1_800_000},
            {"name":"Partner API",                "description":"Открытый API для интеграторов",  "status":"ACTIVE",    "budget":2_100_000},
            {"name":"Редизайн лендинга",          "description":"Новый сайт и онбординг",         "status":"COMPLETED", "budget":650_000},
        ]
        pr_ok = 0
        for pr in projects:
            res = await post(c, f"{BASE}/companies/{cid}/projects", pr, h)
            if res: pr_ok += 1
        print(f"  ✓ {pr_ok}/{len(projects)}")

        # ── СОТРУДНИКИ ────────────────────────────────────────
        print("\n── Сотрудники...")
        employees = [
            {"name":"Иванов Дмитрий Александрович", "position":"CEO",              "base_salary":350000},
            {"name":"Смирнова Екатерина Игоревна",  "position":"CFO",              "base_salary":280000},
            {"name":"Козлов Максим Сергеевич",      "position":"CTO",              "base_salary":320000},
            {"name":"Новикова Анна Петровна",       "position":"Head of Sales",    "base_salary":220000},
            {"name":"Морозов Павел Владимирович",   "position":"Lead Developer",   "base_salary":200000},
            {"name":"Волкова Мария Олеговна",       "position":"Senior Developer", "base_salary":185000},
            {"name":"Соколов Артём Николаевич",    "position":"Account Manager",   "base_salary":130000},
            {"name":"Лебедева Ольга Андреевна",    "position":"Marketing Manager", "base_salary":140000},
            {"name":"Попов Кирилл Дмитриевич",     "position":"Product Manager",   "base_salary":175000},
            {"name":"Зайцева Наталья Юрьевна",     "position":"Customer Success",  "base_salary":110000},
        ]
        emp_ok = 0
        for emp in employees:
            res = await post(c, f"{BASE}/companies/{cid}/employees", emp, h)
            if res: emp_ok += 1
        print(f"  ✓ {emp_ok}/{len(employees)}")

        # ── CRM СДЕЛКИ ────────────────────────────────────────
        print("\n── CRM сделки...")
        deals = [
            {"title":"Axiom Enterprise — ТехноГрупп",    "counterparty_name":"ТехноГрупп ООО",  "amount":2400000,"probability":85,"status":"negotiation","expected_close_date":str(today+timedelta(days=14))},
            {"title":"Лицензия 50 мест — АльфаБанк",     "counterparty_name":"АльфаБанк",       "amount":1800000,"probability":70,"status":"proposal",   "expected_close_date":str(today+timedelta(days=21))},
            {"title":"Интеграция + поддержка — Росатом", "counterparty_name":"Росатом",         "amount":5500000,"probability":60,"status":"negotiation","expected_close_date":str(today+timedelta(days=30))},
            {"title":"SaaS 100 юзеров — Ситроникс",     "counterparty_name":"Ситроникс",        "amount":960000, "probability":90,"status":"proposal",   "expected_close_date":str(today+timedelta(days=7))},
            {"title":"Пилот — Газпром Нефть",            "counterparty_name":"Газпром Нефть",   "amount":800000, "probability":40,"status":"contact",    "expected_close_date":str(today+timedelta(days=45))},
            {"title":"Axiom Finance — МТС",              "counterparty_name":"МТС",             "amount":3200000,"probability":55,"status":"proposal",   "expected_close_date":str(today+timedelta(days=35))},
            {"title":"Подписка — ВКонтакте",             "counterparty_name":"ВКонтакте",       "amount":420000, "probability":100,"status":"won",       "expected_close_date":str(today-timedelta(days=5))},
            {"title":"Лицензия — Яндекс",                "counterparty_name":"Яндекс",          "amount":1100000,"probability":100,"status":"won",       "expected_close_date":str(today-timedelta(days=12))},
            {"title":"Пилот — Сбер",                     "counterparty_name":"Сбербанк",        "amount":650000, "probability":100,"status":"won",       "expected_close_date":str(today-timedelta(days=20))},
            {"title":"Тест — StartupXYZ",                "counterparty_name":"StartupXYZ",      "amount":120000, "probability":0,  "status":"lost",      "expected_close_date":str(today-timedelta(days=15))},
        ]
        crm_ok = 0
        for deal in deals:
            res = await post(c, f"{BASE}/companies/{cid}/crm/deals", deal, h)
            if res: crm_ok += 1
        print(f"  ✓ {crm_ok}/{len(deals)}")

        # ── ПЛАТЁЖНЫЙ КАЛЕНДАРЬ ───────────────────────────────
        print("\n── Платёжный календарь...")
        prs = [
            {"description":"Зарплата — июль 2026",        "amount":1420000,"planned_date":str(today+timedelta(days=10)),"transaction_type":"EXPENSE"},
            {"description":"Аренда офиса — июль",          "amount":320000, "planned_date":str(today+timedelta(days=3)), "transaction_type":"EXPENSE"},
            {"description":"Оплата от ТехноГрупп",        "amount":2400000,"planned_date":str(today+timedelta(days=14)),"transaction_type":"INCOME"},
            {"description":"Оплата от Ситроникс",          "amount":960000, "planned_date":str(today+timedelta(days=7)), "transaction_type":"INCOME"},
            {"description":"Маркетинг Q3",                 "amount":450000, "planned_date":str(today+timedelta(days=20)),"transaction_type":"EXPENSE"},
            {"description":"Серверы AWS — июль",           "amount":185000, "planned_date":str(today+timedelta(days=5)), "transaction_type":"EXPENSE"},
            {"description":"Аванс от МТС (пилот)",         "amount":800000, "planned_date":str(today+timedelta(days=25)),"transaction_type":"INCOME"},
            {"description":"Налоги Q2",                    "amount":380000, "planned_date":str(today+timedelta(days=8)), "transaction_type":"EXPENSE"},
        ]
        pm_ok = 0
        for pm in prs:
            res = await post(c, f"{BASE}/companies/{cid}/payment-requests", pm, h)
            if res: pm_ok += 1
        print(f"  ✓ {pm_ok}/{len(prs)}")

        # ── АЛЕРТЫ ────────────────────────────────────────────
        print("\n── Алерты...")
        alert_rules = [
            {"metric_type":"revenue",      "threshold_value":1500000},
            {"metric_type":"expenses",     "threshold_value":2500000},
            {"metric_type":"cash_balance", "threshold_value":500000},
            {"metric_type":"profit_margin","threshold_value":15.0},
        ]
        al_ok = 0
        for al in alert_rules:
            res = await post(c, f"{BASE}/companies/{cid}/alerts/rules", al, h)
            if res: al_ok += 1
        print(f"  ✓ {al_ok}/{len(alert_rules)}")

        # ── ИТОГ ──────────────────────────────────────────────
        print("\n" + "═"*56)
        print("  ✅  AXIOM FINANCE — INVESTOR DEMO READY")
        print("═"*56)
        print(f"  URL    : http://localhost:8000/static/index.html")
        print(f"  Login  : {EMAIL}")
        print(f"  Pass   : {PASSWORD}")
        print(f"\n  Loaded:")
        print(f"   • 6 months transactions  (+15%/mo revenue growth)")
        print(f"   • {crm_ok} CRM deals  (pipeline + won + lost)")
        print(f"   • {emp_ok} employees")
        print(f"   • {pr_ok} projects")
        print(f"   • {cp_ok} counterparties")
        print(f"   • {pm_ok} scheduled payments in calendar")
        print(f"   • {al_ok} alert rules")

asyncio.run(main())
