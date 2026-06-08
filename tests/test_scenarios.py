"""
Пользовательские сценарии Axiom Finance.

Имитируем реальное поведение людей в системе:

  Сценарий 1 — Дмитрий, владелец кофейни
    Регистрируется → создаёт счета → вносит обороты за месяц →
    смотрит отчёты → приглашает бухгалтера → бухгалтер вносит расходы →
    смотрит итог

  Сценарий 2 — Анна, ИТ-консультант (самозанятая, НПД)
    Регистрируется с НПД → вносит проекты → смотрит налог

  Сценарий 3 — Роман, директор стройкомпании (ОСНО)
    Регистрируется с ОСНО → вносит крупные контракты →
    приглашает менеджера → менеджер смотрит ДДС но не видит Баланс →
    меняет налоговый режим → смотрит новый расчёт налога

  Сценарий 4 — Попытка взлома
    Хакер пробует угадать пароль → брутфорс блокируется →
    пробует лезть в чужую компанию → получает 403
"""
import asyncio, os, time, httpx

for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
    os.environ.pop(k, None)

BASE = "http://127.0.0.1:8011"
TS   = int(time.time())

PASS = FAIL = 0
FAILURES = []

def ok(name):
    global PASS; PASS += 1
    print(f"  \033[32mOK\033[0m  {name}")

def fail(name, detail=""):
    global FAIL; FAIL += 1
    FAILURES.append((name, detail))
    print(f"  \033[31mFAIL\033[0m {name}  — {detail[:120]}")

def check(name, cond, detail=""):
    ok(name) if cond else fail(name, detail)

def scene(title):
    print(f"\n{'='*62}")
    print(f"  {title}")
    print(f"{'='*62}")

def step(title):
    print(f"\n  >> {title}")

def h(token):
    return {"Authorization": f"Bearer {token}"}


async def login_safe(c, email, password, label=""):
    """Логин с обработкой ошибок."""
    r = await c.post("/api/v1/auth/token", data={"username": email, "password": password})
    if r.status_code == 200:
        return r.json()["access_token"]
    check(f"Вход {label}", False, f"[{r.status_code}] {r.text[:80]}")
    return None


async def run():
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=20) as c:

        # ══════════════════════════════════════════════════════════
        scene("СЦЕНАРИЙ 1 — Дмитрий, владелец кофейни «Брю»")
        # ══════════════════════════════════════════════════════════
        # УСН Доходы 6%

        step("Дмитрий открывает сайт и регистрирует кофейню")
        r = await c.post("/api/v1/auth/register", json={
            "email": f"dmitry{TS}@brew.ru",
            "password": "Kofe2026!",
            "full_name": "Дмитрий Орлов",
            "company_name": "Кофейня Брю",
            "tax_regime": "usn_income",
        })
        check("Регистрация кофейни", r.status_code in (200,201), r.text[:80])

        step("Дмитрий входит в систему")
        dtoken = await login_safe(c, f"dmitry{TS}@brew.ru", "Kofe2026!", "Дмитрий")
        if not dtoken: return
        check("Вход выполнен", True)
        dcid   = (await c.get("/api/v1/auth/me", headers=h(dtoken))).json()["company_id"]

        step("Дмитрий создаёт расчётный счёт в Сбербанке и кассу")
        r = await c.post(f"/api/v1/companies/{dcid}/accounts", headers=h(dtoken), json={
            "name": "Сбербанк р/с", "account_type": "bank", "currency_code": "RUB", "balance": 150000
        })
        check("Счёт в Сбере создан", r.status_code in (200,201), r.text[:80])
        sber_id = r.json().get("id")

        r = await c.post(f"/api/v1/companies/{dcid}/accounts", headers=h(dtoken), json={
            "name": "Касса", "account_type": "cash", "currency_code": "RUB", "balance": 25000
        })
        check("Касса создана", r.status_code in (200,201))
        cash_id = r.json().get("id")

        step("Дмитрий создаёт категории расходов и доходов")
        cats = {}
        for name, ctype in [
            ("Продажа кофе", "income"), ("Доставка зерна", "income"),
            ("Аренда помещения", "expense"), ("Зарплата бариста", "expense"),
            ("Закупка зерна", "expense"), ("Коммунальные услуги", "expense"),
        ]:
            r = await c.post(f"/api/v1/companies/{dcid}/categories", headers=h(dtoken), json={"name": name, "category_type": ctype})
            if r.status_code in (200,201):
                cats[name] = r.json()["id"]
        check(f"Категорий создано: {len(cats)}/6", len(cats) == 6)

        step("Дмитрий вносит доходы и расходы за январь")
        txs_jan = [
            # Доходы — касса и счёт
            (sber_id, 380000, "INCOME",  "Продажа кофе через терминал", "2026-01-31", "Продажа кофе"),
            (cash_id, 95000,  "INCOME",  "Продажа кофе за наличные",   "2026-01-31", "Продажа кофе"),
            # Расходы
            (sber_id, 80000,  "EXPENSE", "Аренда январь",              "2026-01-05", "Аренда помещения"),
            (sber_id, 120000, "EXPENSE", "Зарплата бариста январь",    "2026-01-31", "Зарплата бариста"),
            (sber_id, 65000,  "EXPENSE", "Закупка зерна январь",       "2026-01-10", "Закупка зерна"),
            (sber_id, 12000,  "EXPENSE", "Коммунальные платежи",       "2026-01-15", "Коммунальные услуги"),
        ]
        created = 0
        for acc, amount, ttype, desc, pdate, cat_name in txs_jan:
            r = await c.post(f"/api/v1/companies/{dcid}/transactions", headers=h(dtoken), json={
                "account_id": acc, "amount": amount, "transaction_type": ttype,
                "description": desc, "payment_date": pdate,
                "category_id": cats.get(cat_name),
            })
            if r.status_code in (200,201): created += 1
        check(f"Транзакции январь: {created}/6 создано", created == 6)

        step("Дмитрий открывает P&L отчёт чтобы посмотреть прибыль")
        r = await c.get(f"/api/v1/companies/{dcid}/reports/pl?start_date=2026-01-01&end_date=2026-01-31", headers=h(dtoken))
        check("P&L отчёт загрузился", r.status_code == 200)
        if r.status_code == 200:
            pl = r.json()
            revenue  = pl.get("revenue", 0)
            expenses = pl.get("opex", 0)
            net      = pl.get("net_income", 0)
            taxes    = pl.get("taxes", 0)
            check(f"Выручка 475 000 ₽ (терминал+наличные)", abs(revenue - 475000) < 1, f"revenue={revenue}")
            check(f"Расходы 277 000 ₽", abs(expenses - 277000) < 1, f"opex={expenses}")
            check("Налог УСН 6% рассчитан", taxes > 0, f"taxes={taxes}")
            print(f"      Выручка: {revenue:,.0f} ₽  |  Расходы: {expenses:,.0f} ₽  |  Налог: {taxes:,.0f} ₽  |  Чистая прибыль: {net:,.0f} ₽")

        step("Дмитрий смотрит ДДС — откуда пришли и куда ушли деньги")
        r = await c.get(f"/api/v1/companies/{dcid}/reports/cashflow?start_date=2026-01-01&end_date=2026-01-31", headers=h(dtoken))
        check("ДДС загрузился", r.status_code == 200)
        if r.status_code == 200:
            cf = r.json()
            print(f"      Приток: {cf.get('inflow',0):,.0f} ₽  |  Отток: {cf.get('outflow',0):,.0f} ₽  |  Net CF: {cf.get('net_cash_flow',0):,.0f} ₽")

        step("Дмитрий приглашает бухгалтера Ирину")
        r = await c.post(f"/api/v1/companies/{dcid}/team/invite", headers=h(dtoken), json={
            "email": f"irina{TS}@accounting.ru", "role": "accountant"
        })
        check("Инвайт бухгалтеру отправлен", r.status_code in (200,201), r.text[:80])
        inv_token = r.json().get("token") if r.status_code in (200,201) else None

        step("Ирина получает письмо и принимает приглашение")
        if inv_token:
            r = await c.post("/api/v1/auth/accept-invite", json={
                "token": inv_token, "password": "Buh2026!", "full_name": "Ирина Бухгалтер"
            })
            check("Ирина зарегистрировалась", r.status_code in (200,201), r.text[:80])
            itoken = r.json().get("access_token")

            step("Ирина вносит расходы за февраль")
            txs_feb = [
                (sber_id, 390000, "INCOME",  "Продажа кофе февраль",   "2026-02-28", "Продажа кофе"),
                (sber_id, 80000,  "EXPENSE", "Аренда февраль",         "2026-02-05", "Аренда помещения"),
                (sber_id, 120000, "EXPENSE", "Зарплата февраль",       "2026-02-28", "Зарплата бариста"),
                (sber_id, 58000,  "EXPENSE", "Закупка зерна февраль",  "2026-02-12", "Закупка зерна"),
            ]
            created_feb = 0
            for acc, amount, ttype, desc, pdate, cat_name in txs_feb:
                r = await c.post(f"/api/v1/companies/{dcid}/transactions", headers=h(itoken), json={
                    "account_id": acc, "amount": amount, "transaction_type": ttype,
                    "description": desc, "payment_date": pdate,
                    "category_id": cats.get(cat_name),
                })
                if r.status_code in (200,201): created_feb += 1
            check(f"Ирина внесла {created_feb}/4 операции февраля", created_feb == 4)

            step("Ирина пробует пригласить ещё кого-то — не должна иметь права")
            r = await c.post(f"/api/v1/companies/{dcid}/team/invite", headers=h(itoken), json={
                "email": "random@test.ru", "role": "viewer"
            })
            check("Бухгалтер не может приглашать (403)", r.status_code == 403, f"got {r.status_code}")

        step("Дмитрий смотрит P&L за оба месяца")
        r = await c.get(f"/api/v1/companies/{dcid}/reports/pl?start_date=2026-01-01&end_date=2026-02-28", headers=h(dtoken))
        check("P&L за 2 месяца", r.status_code == 200)
        if r.status_code == 200:
            pl2 = r.json()
            print(f"      2 месяца — Выручка: {pl2.get('revenue',0):,.0f} ₽  |  Налог: {pl2.get('taxes',0):,.0f} ₽  |  Прибыль: {pl2.get('net_income',0):,.0f} ₽")

        step("Дмитрий выходит из системы")
        r = await c.post("/api/v1/auth/logout", headers=h(dtoken))
        check("Выход выполнен", r.status_code == 200)
        r = await c.get(f"/api/v1/companies/{dcid}/accounts", headers=h(dtoken))
        check("После выхода токен недействителен (401)", r.status_code == 401)


        # ══════════════════════════════════════════════════════════
        scene("СЦЕНАРИЙ 2 — Анна, ИТ-консультант (НПД / самозанятая)")
        # ══════════════════════════════════════════════════════════

        step("Анна регистрируется как самозанятая")
        r = await c.post("/api/v1/auth/register", json={
            "email": f"anna{TS}@freelance.ru",
            "password": "Anna2026!",
            "full_name": "Анна Смирнова",
            "company_name": "ИП Смирнова А.В.",
            "tax_regime": "npd",
        })
        check("Регистрация ИП на НПД", r.status_code in (200,201))
        atoken = await login_safe(c, f"anna{TS}@freelance.ru", "Anna2026!", "Анна")
        if not atoken: return
        acid   = (await c.get("/api/v1/auth/me", headers=h(atoken))).json()["company_id"]

        step("Анна создаёт счёт")
        r = await c.post(f"/api/v1/companies/{acid}/accounts", headers=h(atoken), json={
            "name": "Тинькофф", "account_type": "bank", "currency_code": "RUB", "balance": 0
        })
        check("Счёт создан", r.status_code in (200,201))
        tink_id = r.json().get("id")

        step("Анна вносит оплаты от клиентов за проекты")
        projects = [
            ("Разработка CRM для ООО Альфа", 120000, "2026-01-20"),
            ("Аудит ИТ-инфраструктуры Бета",  85000, "2026-02-05"),
            ("Интеграция 1С для Гамма",        95000, "2026-02-18"),
            ("Консалтинг Дельта (физлицо)",    30000, "2026-03-01"),
        ]
        proj_created = 0
        for desc, amount, pdate in projects:
            r = await c.post(f"/api/v1/companies/{acid}/transactions", headers=h(atoken), json={
                "account_id": tink_id, "amount": amount,
                "transaction_type": "INCOME",
                "description": desc, "payment_date": pdate,
            })
            if r.status_code in (200,201): proj_created += 1
        check(f"Проектов внесено: {proj_created}/4", proj_created == 4)

        step("Анна проверяет налог НПД")
        r = await c.get(f"/api/v1/companies/{acid}/reports/pl?start_date=2026-01-01&end_date=2026-03-31", headers=h(atoken))
        check("P&L загрузился", r.status_code == 200)
        if r.status_code == 200:
            pl = r.json()
            total = pl.get("revenue", 0)
            tax   = pl.get("taxes", 0)
            print(f"      НПД — Доход: {total:,.0f} ₽  |  Налог 6%: {tax:,.0f} ₽  |  На руки: {total-tax:,.0f} ₽")
            check("Налог НПД > 0", tax > 0, f"tax={tax}")

        step("Анна решает добавить контрагента — ООО Альфа")
        r = await c.post(f"/api/v1/companies/{acid}/counterparties", headers=h(atoken), json={
            "name": "ООО Альфа", "inn": "7701234567", "type": "customer"
        })
        check("Контрагент создан", r.status_code in (200,201), r.text[:80])


        # ══════════════════════════════════════════════════════════
        scene("СЦЕНАРИЙ 3 — Роман, директор стройкомпании (ОСНО)")
        # ══════════════════════════════════════════════════════════

        step("Роман регистрирует строительную компанию на ОСНО")
        r = await c.post("/api/v1/auth/register", json={
            "email": f"roman{TS}@stroy.ru",
            "password": "Stroy2026!",
            "full_name": "Роман Козлов",
            "company_name": "ООО СтройГрупп",
            "tax_regime": "osn",
        })
        check("Регистрация ОСНО", r.status_code in (200,201))
        rtoken = await login_safe(c, f"roman{TS}@stroy.ru", "Stroy2026!", "Роман")
        if not rtoken: return
        check("Роман вошёл", True)
        rcid   = (await c.get("/api/v1/auth/me", headers=h(rtoken))).json()["company_id"]

        step("Роман создаёт счета компании")
        r = await c.post(f"/api/v1/companies/{rcid}/accounts", headers=h(rtoken), json={
            "name": "ВТБ р/с", "account_type": "bank", "currency_code": "RUB", "balance": 5000000
        })
        vtb_id = r.json().get("id") if r.status_code in (200,201) else None
        check("Счёт ВТБ создан", r.status_code in (200,201))

        step("Роман вносит крупные контракты и расходы")
        contracts = [
            (vtb_id, 3500000, "INCOME",  "Контракт №1 — жилой дом",     "2026-01-15"),
            (vtb_id, 2800000, "INCOME",  "Контракт №2 — офисный центр",  "2026-02-20"),
            (vtb_id, 1200000, "EXPENSE", "Материалы — бетон и арматура", "2026-01-20"),
            (vtb_id, 800000,  "EXPENSE", "Субподряд — электрика",        "2026-02-10"),
            (vtb_id, 600000,  "EXPENSE", "Зарплата бригады",             "2026-01-31"),
            (vtb_id, 600000,  "EXPENSE", "Зарплата бригады",             "2026-02-28"),
            (vtb_id, 150000,  "EXPENSE", "Аренда техники",               "2026-01-25"),
        ]
        c_created = 0
        for acc, amount, ttype, desc, pdate in contracts:
            if not acc: continue
            r = await c.post(f"/api/v1/companies/{rcid}/transactions", headers=h(rtoken), json={
                "account_id": acc, "amount": amount, "transaction_type": ttype,
                "description": desc, "payment_date": pdate,
            })
            if r.status_code in (200,201): c_created += 1
        check(f"Операций создано: {c_created}/7", c_created == 7)

        step("Роман смотрит P&L — налог на прибыль ОСНО 20%")
        r = await c.get(f"/api/v1/companies/{rcid}/reports/pl?start_date=2026-01-01&end_date=2026-02-28", headers=h(rtoken))
        check("P&L ОСНО загрузился", r.status_code == 200)
        if r.status_code == 200:
            pl = r.json()
            print(f"      ОСНО — Выручка: {pl.get('revenue',0):,.0f} ₽  |  Налог 20%: {pl.get('taxes',0):,.0f} ₽  |  Прибыль: {pl.get('net_income',0):,.0f} ₽")
            check("Налог ОСНО 20%", pl.get("taxes", 0) > 0)

        step("Роман приглашает менеджера Сергея")
        r = await c.post(f"/api/v1/companies/{rcid}/team/invite", headers=h(rtoken), json={
            "email": f"sergey{TS}@stroy.ru", "role": "manager"
        })
        check("Инвайт менеджеру", r.status_code in (200,201))
        if r.status_code in (200,201):
            minv = r.json().get("token")
            r2 = await c.post("/api/v1/auth/accept-invite", json={
                "token": minv, "password": "Mgr2026!", "full_name": "Сергей Менеджер"
            })
            check("Сергей принял инвайт", r2.status_code in (200,201))
            mtoken = r2.json().get("access_token") if r2.status_code in (200,201) else None

            if mtoken:
                step("Сергей (MANAGER) смотрит ДДС — должен видеть")
                r = await c.get(f"/api/v1/companies/{rcid}/reports/cashflow?start_date=2026-01-01&end_date=2026-02-28", headers=h(mtoken))
                check("Менеджер видит ДДС", r.status_code == 200, f"got {r.status_code}")

                step("Сергей пытается открыть Баланс — не должен видеть")
                r = await c.get(f"/api/v1/companies/{rcid}/reports/balance?date=2026-02-28", headers=h(mtoken))
                check("Менеджер НЕ видит Баланс (403)", r.status_code == 403, f"got {r.status_code}")

                step("Сергей пытается удалить транзакцию — не должен")
                r_txs = await c.get(f"/api/v1/companies/{rcid}/transactions", headers=h(rtoken))
                txs = r_txs.json() if isinstance(r_txs.json(), list) else []
                if txs:
                    tx_id = txs[0].get("id") or txs[0].get("transaction_id")
                    r = await c.delete(f"/api/v1/companies/{rcid}/transactions/{tx_id}", headers=h(mtoken))
                    check("Менеджер не может удалить транзакцию (403)", r.status_code == 403, f"got {r.status_code}")

        step("Роман решает перейти на УСН 15% (Доходы−Расходы)")
        r = await c.patch(f"/api/v1/companies/{rcid}/tax-regime", headers=h(rtoken), json={"tax_regime": "usn_profit"})
        check("Смена режима ОСНО → УСН 15%", r.status_code < 400)

        step("Роман смотрит P&L уже с новым режимом УСН 15%")
        r = await c.get(f"/api/v1/companies/{rcid}/reports/pl?start_date=2026-01-01&end_date=2026-02-28", headers=h(rtoken))
        if r.status_code == 200:
            pl_new = r.json()
            print(f"      УСН 15% — Налог: {pl_new.get('taxes',0):,.0f} ₽  (был ОСНО 20%: {pl.get('taxes',0):,.0f} ₽)")
            check("Налог изменился после смены режима", pl_new.get("taxes",0) != pl.get("taxes",0), "taxes одинаковые")


        # ══════════════════════════════════════════════════════════
        scene("СЦЕНАРИЙ 4 — Попытка взлома")
        # ══════════════════════════════════════════════════════════

        step("Хакер пытается перебрать пароль Дмитрия (35 попыток — превышает лимит 30/мин)")
        blocked = 0
        for i in range(35):
            r = await c.post("/api/v1/auth/token", data={
                "username": f"dmitry{TS}@brew.ru",
                "password": f"wrongpass{i}"
            })
            if r.status_code == 429:
                blocked += 1
        check(f"Брутфорс заблокирован rate limit (429)", blocked > 0, f"заблокировано {blocked}/35 попыток")

        step("Хакер знает company_id Дмитрия и пытается влезть со своим токеном")
        # Регистрируем хакера
        r = await c.post("/api/v1/auth/register", json={
            "email": f"hacker{TS}@evil.ru", "password": "Hack2026!",
            "full_name": "Злой Хакер", "company_name": "ООО Хак",
        })
        r2 = await c.post("/api/v1/auth/token", data={"username": f"hacker{TS}@evil.ru", "password": "Hack2026!"})
        htoken = r2.json().get("access_token", "")

        if htoken:
            # Пробуем читать транзакции Дмитрия
            r = await c.get(f"/api/v1/companies/{dcid}/transactions", headers=h(htoken))
            check("Хакер не может читать чужие транзакции (403)", r.status_code == 403, f"got {r.status_code}")

            # Пробуем создать транзакцию в чужой компании
            if sber_id:
                r = await c.post(f"/api/v1/companies/{dcid}/transactions", headers=h(htoken), json={
                    "account_id": sber_id, "amount": 999999,
                    "transaction_type": "EXPENSE", "description": "вывод денег хакером"
                })
                check("Хакер не может создать транзакцию (403)", r.status_code == 403, f"got {r.status_code}")

            # Пробуем пригласить себя в чужую команду
            r = await c.post(f"/api/v1/companies/{dcid}/team/invite", headers=h(htoken), json={
                "email": f"hacker{TS}@evil.ru", "role": "owner"
            })
            check("Хакер не может добавить себя в команду (403)", r.status_code == 403, f"got {r.status_code}")

            # Пробуем сменить налоговый режим чужой компании
            r = await c.patch(f"/api/v1/companies/{dcid}/tax-regime", headers=h(htoken), json={"tax_regime": "npd"})
            check("Хакер не может менять настройки (403)", r.status_code == 403, f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════
        # ИТОГ
        # ══════════════════════════════════════════════════════════
        total = PASS + FAIL
        print(f"\n{'═'*62}")
        print(f"  ПОЛЬЗОВАТЕЛЬСКИЕ СЦЕНАРИИ: {total} проверок")
        print(f"  \033[32mPASS: {PASS}\033[0m   \033[31mFAIL: {FAIL}\033[0m")
        if FAILURES:
            print("\n  Проблемы:")
            for name, detail in FAILURES:
                print(f"    ✗ {name}")
                if detail: print(f"      {detail}")
        print(f"{'═'*62}\n")

asyncio.run(run())
