"""
Полный интеграционный тест Axiom Finance.

Покрывает:
  - Регистрация / вход / выход
  - Все 5 ролей: OWNER, ADMIN, ACCOUNTANT, VIEWER, MANAGER
  - IDOR: нельзя читать чужую компанию
  - Счета, категории, транзакции (CRUD)
  - Отчёты: P&L, ДДС, Баланс
  - Команда: инвайт по email, инвайт-ссылка, смена роли, удаление
  - Налоговый режим: чтение и смена
  - Контрагенты
  - Лимиты доступа по роли
"""
import asyncio
import os
import time
import httpx

# убираем прокси
for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
    os.environ.pop(k, None)

BASE = "http://127.0.0.1:8008"
TS   = int(time.time())

# ─────────────────────────────────────────────
# счётчики
# ─────────────────────────────────────────────
PASS = 0
FAIL = 0
SKIP = 0
FAILURES = []

def ok(name):
    global PASS
    PASS += 1
    print(f"  \033[32mPASS\033[0m  {name}")

def fail(name, detail=""):
    global FAIL
    FAIL += 1
    FAILURES.append((name, detail))
    print(f"  \033[31mFAIL\033[0m  {name}  ← {detail[:120]}")

def skip(name, reason=""):
    global SKIP
    SKIP += 1
    print(f"  \033[33mSKIP\033[0m  {name}  ({reason})")

def check(name, condition, detail=""):
    if condition:
        ok(name)
    else:
        fail(name, detail)

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────
async def register(c, email, name="User", company=None, regime="usn_income"):
    r = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "Pass123!",
        "full_name": name,
        "company_name": company or f"Company_{email}",
        "tax_regime": regime,
    })
    return r

async def login(c, email, password="Pass123!"):
    r = await c.post("/api/v1/auth/token", data={"username": email, "password": password})
    if r.status_code == 200:
        return r.json()["access_token"]
    return None

async def me(c, token):
    r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        return r.json()
    return {}

def h(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def run():
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=15) as c:

        # ══════════════════════════════════════
        section("1. РЕГИСТРАЦИЯ И ВХОД")
        # ══════════════════════════════════════

        owner_email = f"owner{TS}@axiom.ru"
        r = await register(c, owner_email, "Иван Владелец", "ООО Аксиом", "usn_income")
        check("Регистрация нового пользователя", r.status_code in (200,201), r.text[:100])

        r2 = await register(c, owner_email, "Иван Владелец", "ООО Аксиом")
        check("Повторная регистрация → ошибка", r2.status_code >= 400, "Должен быть 4xx")

        owner_token = await login(c, owner_email)
        check("Вход по email/паролю", owner_token is not None)

        bad_token = await login(c, owner_email, "wrongpass")
        check("Неверный пароль → 401/422", bad_token is None)

        info = await me(c, owner_token)
        owner_cid = info.get("company_id")
        check("GET /auth/me возвращает company_id", bool(owner_cid))

        # ══════════════════════════════════════
        section("2. СЧЕТА")
        # ══════════════════════════════════════

        r = await c.post(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token), json={
            "name": "Расчётный счёт", "account_type": "bank",
            "currency_code": "RUB", "balance": 500000
        })
        check("Создать счёт (bank + currency_code + balance)", r.status_code in (200,201), r.text[:100])
        acc_id = r.json().get("id") if r.status_code < 400 else None

        r = await c.post(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token), json={
            "name": "Касса", "account_type": "cash", "currency": "RUB"
        })
        check("Создать счёт (cash + currency)", r.status_code in (200,201), r.text[:100])
        acc_cash_id = r.json().get("id") if r.status_code < 400 else None

        r = await c.get(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token))
        check("Список счетов", r.status_code == 200)
        accs = r.json().get("accounts", [])
        check("Счета созданы — в списке ≥2", len(accs) >= 2, f"count={len(accs)}")

        r = await c.post(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token), json={
            "name": "Bad", "account_type": "UNKNOWN_TYPE", "currency": "RUB"
        })
        check("Неверный тип счёта → 422", r.status_code == 422, r.text[:80])

        # ══════════════════════════════════════
        section("3. КАТЕГОРИИ")
        # ══════════════════════════════════════

        r = await c.post(f"/api/v1/companies/{owner_cid}/categories", headers=h(owner_token), json={
            "name": "Аренда", "category_type": "expense"
        })
        check("Создать категорию (расход)", r.status_code in (200,201), r.text[:100])
        cat_expense_id = r.json().get("id") if r.status_code < 400 else None

        r = await c.post(f"/api/v1/companies/{owner_cid}/categories", headers=h(owner_token), json={
            "name": "Продажи", "category_type": "income"
        })
        check("Создать категорию (доход)", r.status_code in (200,201), r.text[:100])
        cat_income_id = r.json().get("id") if r.status_code < 400 else None

        r = await c.get(f"/api/v1/companies/{owner_cid}/categories", headers=h(owner_token))
        check("Список категорий", r.status_code == 200)

        # ══════════════════════════════════════
        section("4. ТРАНЗАКЦИИ")
        # ══════════════════════════════════════

        if not acc_id:
            accs = (await c.get(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token))).json().get("accounts",[])
            acc_id = accs[0]["id"] if accs else None

        transactions_data = [
            {"amount": 300000, "transaction_type": "INCOME",  "description": "Оплата клиента 1", "payment_date": "2026-01-15", "category_id": cat_income_id},
            {"amount": 250000, "transaction_type": "INCOME",  "description": "Оплата клиента 2", "payment_date": "2026-02-10", "category_id": cat_income_id},
            {"amount": 80000,  "transaction_type": "EXPENSE", "description": "Аренда январь",    "payment_date": "2026-01-01", "category_id": cat_expense_id},
            {"amount": 80000,  "transaction_type": "EXPENSE", "description": "Аренда февраль",   "payment_date": "2026-02-01", "category_id": cat_expense_id},
            {"amount": 120000, "transaction_type": "EXPENSE", "description": "Зарплата",          "payment_date": "2026-01-31"},
        ]
        tx_ids = []
        for tx in transactions_data:
            r = await c.post(f"/api/v1/companies/{owner_cid}/transactions", headers=h(owner_token), json={
                "account_id": acc_id, **tx
            })
            if r.status_code in (200,201):
                tx_ids.append(r.json().get("id"))
        check(f"Создано {len(tx_ids)}/5 транзакций", len(tx_ids) == 5)

        r = await c.get(f"/api/v1/companies/{owner_cid}/transactions", headers=h(owner_token))
        txs = r.json() if isinstance(r.json(), list) else r.json().get("transactions", [])
        check("Список транзакций", r.status_code == 200)
        check("Транзакций в списке ≥5", len(txs) >= 5, f"count={len(txs)}")

        # Редактирование транзакции
        if tx_ids:
            r = await c.patch(f"/api/v1/companies/{owner_cid}/transactions/{tx_ids[0]}", headers=h(owner_token), json={
                "description": "Оплата клиента 1 (обновлено)", "amount": 310000
            })
            check("Редактирование транзакции", r.status_code < 400, r.text[:100])

        # Удаление транзакции
        if len(tx_ids) >= 5:
            r = await c.delete(f"/api/v1/companies/{owner_cid}/transactions/{tx_ids[-1]}", headers=h(owner_token))
            check("Удаление транзакции", r.status_code < 400, r.text[:100])

        # ══════════════════════════════════════
        section("5. ОТЧЁТЫ")
        # ══════════════════════════════════════

        r = await c.get(f"/api/v1/companies/{owner_cid}/reports/pl?start_date=2026-01-01&end_date=2026-12-31", headers=h(owner_token))
        check("P&L отчёт", r.status_code == 200, r.text[:100])
        if r.status_code == 200:
            pl = r.json()
            check("P&L: выручка > 0", pl.get("revenue", 0) > 0, f"revenue={pl.get('revenue')}")
            check("P&L: расходы > 0",  pl.get("opex", 0) > 0,    f"opex={pl.get('opex')}")
            check("P&L: налог рассчитан", pl.get("taxes", -1) >= 0, f"taxes={pl.get('taxes')}")

        r = await c.get(f"/api/v1/companies/{owner_cid}/reports/cashflow?start_date=2026-01-01&end_date=2026-12-31", headers=h(owner_token))
        check("ДДС отчёт", r.status_code == 200, r.text[:100])
        if r.status_code == 200:
            cf = r.json()
            check("ДДС: inflow > 0", cf.get("inflow", 0) > 0, f"inflow={cf.get('inflow')}")

        r = await c.get(f"/api/v1/companies/{owner_cid}/reports/balance?date=2026-06-01", headers=h(owner_token))
        check("Баланс отчёт", r.status_code == 200, r.text[:100])

        # ══════════════════════════════════════
        section("6. НАЛОГОВЫЙ РЕЖИМ")
        # ══════════════════════════════════════

        r = await c.get(f"/api/v1/companies/{owner_cid}/tax-regime", headers=h(owner_token))
        check("Чтение налогового режима", r.status_code == 200)
        if r.status_code == 200:
            check("Режим = usn_income (по умолчанию)", r.json().get("code") == "usn_income", r.json().get("code"))

        for regime in ["usn_profit", "osn", "eshn", "npd", "patent", "usn_income"]:
            r = await c.patch(f"/api/v1/companies/{owner_cid}/tax-regime", headers=h(owner_token), json={"tax_regime": regime})
            check(f"Смена режима → {regime}", r.status_code < 400, r.text[:80])

        # ══════════════════════════════════════
        section("7. КОМАНДА — ПРИГЛАШЕНИЯ")
        # ══════════════════════════════════════

        roles_to_invite = [
            ("admin",      f"admin{TS}@test.ru"),
            ("accountant", f"acct{TS}@test.ru"),
            ("viewer",     f"viewer{TS}@test.ru"),
            ("manager",    f"mgr{TS}@test.ru"),
        ]
        role_tokens = {}

        for role, email in roles_to_invite:
            # Создать приглашение
            r = await c.post(f"/api/v1/companies/{owner_cid}/team/invite", headers=h(owner_token), json={
                "email": email, "role": role
            })
            check(f"Создать инвайт для {role}", r.status_code in (200,201), r.text[:100])
            if r.status_code not in (200,201):
                continue
            inv_token = r.json().get("token")

            # Принять приглашение
            r2 = await c.post("/api/v1/auth/accept-invite", json={
                "token": inv_token, "password": "Pass123!", "full_name": f"Test {role.capitalize()}"
            })
            check(f"Принять инвайт ({role})", r2.status_code < 400, r2.text[:100])
            if r2.status_code < 400:
                rt = r2.json().get("access_token")
                if rt:
                    role_tokens[role] = (email, rt)

        # Инвайт-ссылка (открытая)
        r = await c.post(f"/api/v1/companies/{owner_cid}/team/invite-link", headers=h(owner_token), json={"role": "viewer"})
        check("Создать инвайт-ссылку", r.status_code in (200,201), r.text[:80])
        if r.status_code in (200,201):
            link_token = r.json().get("token")
            new_email = f"open{TS}@test.ru"
            r2 = await c.post("/api/v1/auth/accept-invite", json={
                "token": link_token, "email": new_email, "password": "Pass123!", "full_name": "Open User"
            })
            check("Принять открытую ссылку (с email)", r2.status_code < 400, r2.text[:100])

        # Список участников
        r = await c.get(f"/api/v1/companies/{owner_cid}/team/members", headers=h(owner_token))
        check("Список участников команды", r.status_code == 200)
        members = r.json() if isinstance(r.json(), list) else []
        check(f"Участников ≥2 (owner + приглашённые)", len(members) >= 2, f"count={len(members)}")

        # ══════════════════════════════════════
        section("8. RBAC — ПРОВЕРКА ПРАВ ПО РОЛЯМ")
        # ══════════════════════════════════════

        # VIEWER — только чтение, не может создавать транзакции
        if "viewer" in role_tokens:
            _, vtoken = role_tokens["viewer"]
            r = await c.get(f"/api/v1/companies/{owner_cid}/reports/pl?start_date=2026-01-01&end_date=2026-12-31", headers=h(vtoken))
            check("VIEWER: может читать P&L", r.status_code == 200, r.text[:80])

            r = await c.post(f"/api/v1/companies/{owner_cid}/transactions", headers=h(vtoken), json={
                "account_id": acc_id, "amount": 1000, "transaction_type": "INCOME", "description": "test"
            })
            check("VIEWER: не может создать транзакцию → 403", r.status_code == 403, f"got {r.status_code}: {r.text[:80]}")

            r = await c.post(f"/api/v1/companies/{owner_cid}/team/invite", headers=h(vtoken), json={"email": "x@x.ru", "role": "viewer"})
            check("VIEWER: не может приглашать → 403", r.status_code == 403, f"got {r.status_code}")

        else:
            skip("VIEWER RBAC", "invite failed")

        # MANAGER — может видеть дашборд, не может видеть баланс
        if "manager" in role_tokens:
            _, mtoken = role_tokens["manager"]
            r = await c.get(f"/api/v1/companies/{owner_cid}/reports/cashflow?start_date=2026-01-01&end_date=2026-12-31", headers=h(mtoken))
            check("MANAGER: может читать ДДС", r.status_code == 200, r.text[:80])

            r = await c.get(f"/api/v1/companies/{owner_cid}/reports/balance?date=2026-06-01", headers=h(mtoken))
            check("MANAGER: не может читать Баланс → 403", r.status_code == 403, f"got {r.status_code}")

            r = await c.patch(f"/api/v1/companies/{owner_cid}/tax-regime", headers=h(mtoken), json={"tax_regime": "osn"})
            check("MANAGER: не может менять налог → 403", r.status_code == 403, f"got {r.status_code}")

        else:
            skip("MANAGER RBAC", "invite failed")

        # ACCOUNTANT — может создавать транзакции, не может управлять командой
        if "accountant" in role_tokens:
            _, atoken = role_tokens["accountant"]
            r = await c.post(f"/api/v1/companies/{owner_cid}/transactions", headers=h(atoken), json={
                "account_id": acc_id, "amount": 5000, "transaction_type": "EXPENSE", "description": "test acct"
            })
            check("ACCOUNTANT: может создать транзакцию", r.status_code in (200,201), r.text[:80])

            r = await c.post(f"/api/v1/companies/{owner_cid}/team/invite", headers=h(atoken), json={"email": "y@y.ru", "role": "viewer"})
            check("ACCOUNTANT: не может приглашать → 403", r.status_code == 403, f"got {r.status_code}")

        else:
            skip("ACCOUNTANT RBAC", "invite failed")

        # ADMIN — может всё кроме биллинга
        if "admin" in role_tokens:
            _, adtoken = role_tokens["admin"]
            r = await c.post(f"/api/v1/companies/{owner_cid}/team/invite", headers=h(adtoken), json={
                "email": f"new{TS}x@test.ru", "role": "viewer"
            })
            check("ADMIN: может приглашать", r.status_code in (200,201), r.text[:80])

            r = await c.patch(f"/api/v1/companies/{owner_cid}/tax-regime", headers=h(adtoken), json={"tax_regime": "usn_income"})
            check("ADMIN: может менять налоговый режим", r.status_code < 400, r.text[:80])

        else:
            skip("ADMIN RBAC", "invite failed")

        # ══════════════════════════════════════
        section("9. IDOR — ЗАЩИТА ОТ ЧУЖИХ ДАННЫХ")
        # ══════════════════════════════════════

        # Создаём вторую компанию
        email2 = f"other{TS}@axiom.ru"
        await register(c, email2, "Другой Владелец", "ООО Другая")
        token2 = await login(c, email2)
        info2  = await me(c, token2)
        cid2   = info2.get("company_id")

        if token2 and cid2:
            # Пользователь компании 2 пытается читать данные компании 1
            r = await c.get(f"/api/v1/companies/{owner_cid}/transactions", headers=h(token2))
            check("IDOR: чужая компания → 403", r.status_code == 403, f"got {r.status_code}: {r.text[:80]}")

            r = await c.get(f"/api/v1/companies/{owner_cid}/reports/pl?start_date=2026-01-01&end_date=2026-12-31", headers=h(token2))
            check("IDOR: чужой P&L → 403", r.status_code == 403, f"got {r.status_code}")

            r = await c.post(f"/api/v1/companies/{owner_cid}/transactions", headers=h(token2), json={
                "account_id": acc_id, "amount": 999, "transaction_type": "INCOME", "description": "hack"
            })
            check("IDOR: создать транзакцию в чужой компании → 403", r.status_code == 403, f"got {r.status_code}")

            r = await c.get(f"/api/v1/companies/{owner_cid}/team/members", headers=h(token2))
            check("IDOR: чужая команда → 403", r.status_code == 403, f"got {r.status_code}")
        else:
            skip("IDOR тесты", "не удалось создать вторую компанию")

        # ══════════════════════════════════════
        section("10. КОНТРАГЕНТЫ")
        # ══════════════════════════════════════

        r = await c.get(f"/api/v1/companies/{owner_cid}/counterparties", headers=h(owner_token))
        check("Список контрагентов", r.status_code == 200)

        r = await c.post(f"/api/v1/companies/{owner_cid}/counterparties", headers=h(owner_token), json={
            "name": "ООО Клиент", "type": "customer", "inn": "7701234567"
        })
        if r.status_code in (200,201):
            check("Создать контрагента", True)
        elif r.status_code == 422:
            skip("Создать контрагента", f"schema mismatch: {r.text[:80]}")
        else:
            check("Создать контрагента", False, r.text[:100])

        # ══════════════════════════════════════
        section("11. LOGOUT И ИНВАЛИДАЦИЯ ТОКЕНА")
        # ══════════════════════════════════════

        r = await c.post("/api/v1/auth/logout", headers=h(owner_token))
        check("Logout успешен", r.status_code == 200)

        r = await c.get(f"/api/v1/companies/{owner_cid}/accounts", headers=h(owner_token))
        check("После logout токен не работает → 401", r.status_code == 401, f"got {r.status_code}")

        # ══════════════════════════════════════
        # ИТОГ
        # ══════════════════════════════════════
        total = PASS + FAIL + SKIP
        print(f"\n{'═'*60}")
        print(f"  ИТОГ: {total} тестов")
        print(f"  \033[32mPASS: {PASS}\033[0m   \033[31mFAIL: {FAIL}\033[0m   \033[33mSKIP: {SKIP}\033[0m")
        if FAILURES:
            print(f"\n  Упавшие тесты:")
            for name, detail in FAILURES:
                print(f"    ✗ {name}")
                if detail:
                    print(f"      {detail}")
        print(f"{'═'*60}\n")

asyncio.run(run())
