"""
Тесты: Платёжный календарь — всевозможные сценарии.

Покрываем три подсистемы:

  1. Заявки на оплату (PaymentRequest)
     PENDING → APPROVED → PAID
     PENDING → REJECTED (с причиной)
     Создание, список, фильтрация, одиночный GET, удаление, RBAC

  2. Плановые транзакции (PlannedTransaction / Calendar)
     CSV-импорт, просмотр, пустой календарь

  3. Кредиты и займы (Loan + LoanPaymentSchedule)
     Аннуитет, дифференцированный, график, оплата траншей,
     повторная оплата = ошибка, удаление займа

  + Граничные случаи: прошедшие / будущие даты, нулевая / отрицательная сумма
  + RBAC: VIEWER/MANAGER/ACCOUNTANT не могут то, чего не должны
  + IDOR: чужая компания — 403
"""
import asyncio, os, time, httpx

for k in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
    os.environ.pop(k, None)

BASE = "http://127.0.0.1:8011"
TS   = int(time.time())

PASS = FAIL = 0
FAILURES: list[tuple[str, str]] = []

def ok(name: str):
    global PASS; PASS += 1
    print(f"  \033[32mOK\033[0m  {name}")

def fail(name: str, detail: str = ""):
    global FAIL; FAIL += 1
    FAILURES.append((name, detail))
    print(f"  \033[31mFAIL\033[0m {name}  — {detail[:120]}")

def check(name: str, cond: bool, detail: str = ""):
    ok(name) if cond else fail(name, detail)

def section(title: str):
    print(f"\n{'─'*62}")
    print(f"  {title}")
    print(f"{'─'*62}")

def step(title: str):
    print(f"\n  >> {title}")

def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────

async def register_and_login(c: httpx.AsyncClient, suffix: str, regime: str = "usn_income") -> tuple[str, str]:
    """Регистрирует пользователя и возвращает (token, company_id)."""
    email = f"{suffix}_{TS}@cal.ru"
    await c.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Cal2026!",
        "full_name": suffix,
        "company_name": f"Компания {suffix}",
        "tax_regime": regime,
    })
    r = await c.post("/api/v1/auth/token", data={"username": email, "password": "Cal2026!"})
    if r.status_code != 200:
        raise RuntimeError(f"Не удалось залогиниться: {r.text}")
    data = r.json()
    return data["access_token"], data["company_id"]


async def invite_and_accept(c: httpx.AsyncClient, owner_token: str, cid: str,
                             email: str, role: str, password: str = "Inv2026!") -> str:
    """Создаёт инвайт, принимает его; возвращает access_token нового пользователя."""
    r = await c.post(f"/api/v1/companies/{cid}/team/invite",
                     headers=h(owner_token),
                     json={"email": email, "role": role})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Invite failed: {r.text}")
    inv_token = r.json()["token"]
    r2 = await c.post("/api/v1/auth/accept-invite",
                      json={"token": inv_token, "password": password, "full_name": role.title()})
    if r2.status_code not in (200, 201):
        raise RuntimeError(f"Accept-invite failed: {r2.text}")
    return r2.json()["access_token"]


async def create_account(c: httpx.AsyncClient, token: str, cid: str,
                          name: str = "Основной счёт") -> str:
    """Создаёт банковский счёт; возвращает его id."""
    r = await c.post(f"/api/v1/companies/{cid}/accounts", headers=h(token), json={
        "name": name, "account_type": "bank", "currency_code": "RUB", "balance": 1000000,
    })
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Create account failed: {r.text}")
    return r.json()["id"]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def run():
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=30) as c:

        # ══════════════════════════════════════════════════════════════════════
        section("ПОДГОТОВКА: Регистрируем компании и пользователей")
        # ══════════════════════════════════════════════════════════════════════

        step("Главная компания — Владелец (Алексей)")
        owner_tok, cid = await register_and_login(c, "alexey_cal")
        acc_id = await create_account(c, owner_tok, cid)
        check("Владелец зарегистрирован", True)

        step("Приглашаем сотрудников")
        fin_tok  = await invite_and_accept(c, owner_tok, cid, f"finance_{TS}@cal.ru",    "accountant")
        mgr_tok  = await invite_and_accept(c, owner_tok, cid, f"manager_{TS}@cal.ru",    "manager")
        view_tok = await invite_and_accept(c, owner_tok, cid, f"viewer_{TS}@cal.ru",     "viewer")
        check("Бухгалтер, менеджер, наблюдатель добавлены", True)

        step("Чужая компания (для IDOR-тестов)")
        other_tok, other_cid = await register_and_login(c, "evil_cal")
        check("Чужая компания зарегистрирована", True)


        # ══════════════════════════════════════════════════════════════════════
        section("1. ЗАЯВКИ НА ОПЛАТУ — базовый CRUD")
        # ══════════════════════════════════════════════════════════════════════

        step("1.1 Пустой список заявок при старте")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok))
        check("GET payment-requests: 200", r.status_code == 200)
        check("Список изначально пуст", r.json() == [], f"got {r.json()}")

        step("1.2 Создаём расходную заявку (аренда — завтра)")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 85000,
            "planned_date": "2026-07-01",
            "description": "Аренда офиса июль",
            "transaction_type": "EXPENSE",
        })
        check("Заявка аренда создана (201)", r.status_code == 201, r.text[:80])
        rent_req_id = r.json().get("id") if r.status_code == 201 else None
        if r.status_code == 201:
            check("Статус PENDING", r.json()["status"] == "pending")
            check("Сумма верна", float(r.json()["amount"]) == 85000.0)
            check("Плановая дата сохранена", r.json()["planned_date"] == "2026-07-01")

        step("1.3 Создаём доходную заявку")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 250000,
            "planned_date": "2026-07-05",
            "description": "Оплата от клиента за проект",
            "transaction_type": "INCOME",
        })
        check("Заявка доход создана", r.status_code == 201, r.text[:80])
        income_req_id = r.json().get("id") if r.status_code == 201 else None

        step("1.4 Заявка с прошедшей датой (должна приниматься)")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 15000,
            "planned_date": "2025-12-01",
            "description": "Ретроактивная выплата",
        })
        check("Заявка с прошлой датой принята (201)", r.status_code == 201, r.text[:80])
        past_req_id = r.json().get("id") if r.status_code == 201 else None

        step("1.5 Заявка на далёкое будущее")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 500000,
            "planned_date": "2030-01-01",
            "description": "Стратегический платёж через 4 года",
        })
        check("Заявка на будущую дату принята", r.status_code == 201, r.text[:80])
        future_req_id = r.json().get("id") if r.status_code == 201 else None

        step("1.6 Валидация: нулевая сумма → 422")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 0, "planned_date": "2026-07-01",
        })
        check("Нулевая сумма: 422", r.status_code == 422, f"got {r.status_code}")

        step("1.7 Валидация: отрицательная сумма → 422")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": -1000, "planned_date": "2026-07-01",
        })
        check("Отрицательная сумма: 422", r.status_code == 422, f"got {r.status_code}")

        step("1.8 Валидация: неверный transaction_type → 422")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok), json={
            "amount": 1000, "planned_date": "2026-07-01", "transaction_type": "TRANSFER",
        })
        check("Неверный transaction_type: 422", r.status_code == 422, f"got {r.status_code}")

        step("1.9 GET список: теперь 4 заявки (pending)")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok))
        check("Список заявок: 200", r.status_code == 200)
        count = len(r.json())
        check(f"4 заявки в списке (got {count})", count == 4, f"count={count}")

        step("1.10 Фильтрация: только pending")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests?status=pending", headers=h(owner_tok))
        check("Фильтр pending: 200", r.status_code == 200)
        check("Все 4 — pending", len(r.json()) == 4)

        step("1.11 Фильтрация: approved (должно быть 0)")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests?status=approved", headers=h(owner_tok))
        check("Фильтр approved (пусто)", r.json() == [])

        step("1.12 GET одной заявки по ID")
        if rent_req_id:
            r = await c.get(f"/api/v1/companies/{cid}/payment-requests/{rent_req_id}", headers=h(owner_tok))
            check("GET по ID: 200", r.status_code == 200)
            check("ID совпадает", r.json()["id"] == rent_req_id)
            check("Описание верно", r.json()["description"] == "Аренда офиса июль")

        step("1.13 GET несуществующего ID → 404")
        fake_id = "00000000-0000-0000-0000-000000000001"
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests/{fake_id}", headers=h(owner_tok))
        check("GET несуществующего: 404", r.status_code == 404)


        # ══════════════════════════════════════════════════════════════════════
        section("2. ЗАЯВКИ — ЖИЗНЕННЫЙ ЦИКЛ: Одобрение / Отклонение")
        # ══════════════════════════════════════════════════════════════════════

        step("2.1 Одобряем заявку на аренду")
        if rent_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{rent_req_id}/status",
                              headers=h(owner_tok), json={"status": "approved"})
            check("Одобрение: 200", r.status_code == 200, r.text[:80])
            check("Статус стал approved", r.json().get("status") == "approved")
            check("reviewer_user_id заполнен", r.json().get("reviewer_user_id") is not None)
            check("reviewed_at заполнен", r.json().get("reviewed_at") is not None)

        step("2.2 Повторное одобрение уже одобренной заявки → 422")
        if rent_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{rent_req_id}/status",
                              headers=h(owner_tok), json={"status": "approved"})
            check("Двойное одобрение: 422", r.status_code == 422, f"got {r.status_code}")

        step("2.3 Отклоняем заявку с причиной")
        if past_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{past_req_id}/status",
                              headers=h(owner_tok), json={
                                  "status": "rejected",
                                  "rejection_reason": "Нет средств на счёте",
                              })
            check("Отклонение с причиной: 200", r.status_code == 200, r.text[:80])
            check("Статус стал rejected", r.json().get("status") == "rejected")
            check("Причина сохранена", r.json().get("rejection_reason") == "Нет средств на счёте")

        step("2.4 Отклонение БЕЗ причины → 422")
        if income_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{income_req_id}/status",
                              headers=h(owner_tok), json={"status": "rejected"})
            check("Отклонение без причины: 422", r.status_code == 422, f"got {r.status_code}")

        step("2.5 Фильтрация после изменения статусов")
        r_all      = await c.get(f"/api/v1/companies/{cid}/payment-requests", headers=h(owner_tok))
        r_pending  = await c.get(f"/api/v1/companies/{cid}/payment-requests?status=pending",  headers=h(owner_tok))
        r_approved = await c.get(f"/api/v1/companies/{cid}/payment-requests?status=approved", headers=h(owner_tok))
        r_rejected = await c.get(f"/api/v1/companies/{cid}/payment-requests?status=rejected", headers=h(owner_tok))
        check("Всего 4 заявки", len(r_all.json()) == 4)
        check("2 pending (доход + будущий)", len(r_pending.json()) == 2,  f"got {len(r_pending.json())}")
        check("1 approved (аренда)",         len(r_approved.json()) == 1, f"got {len(r_approved.json())}")
        check("1 rejected (ретро)",           len(r_rejected.json()) == 1, f"got {len(r_rejected.json())}")

        step("2.6 Удаление PENDING-заявки")
        if future_req_id:
            r = await c.delete(f"/api/v1/companies/{cid}/payment-requests/{future_req_id}", headers=h(owner_tok))
            check("Удаление PENDING: 204", r.status_code == 204, f"got {r.status_code}")
            # Проверяем что удалено
            r2 = await c.get(f"/api/v1/companies/{cid}/payment-requests/{future_req_id}", headers=h(owner_tok))
            check("После удаления: 404", r2.status_code == 404)

        step("2.7 Удаление APPROVED-заявки → 422")
        if rent_req_id:
            r = await c.delete(f"/api/v1/companies/{cid}/payment-requests/{rent_req_id}", headers=h(owner_tok))
            check("Удаление APPROVED: 422", r.status_code == 422, f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("3. ЗАЯВКИ — RBAC (роли и права)")
        # ══════════════════════════════════════════════════════════════════════

        step("3.1 МЕНЕДЖЕР создаёт заявку (должен иметь право)")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(mgr_tok), json={
            "amount": 12000,
            "planned_date": "2026-07-10",
            "description": "Расходы менеджера на командировку",
        })
        check("Менеджер создаёт заявку: 201", r.status_code == 201, r.text[:80])
        mgr_req_id = r.json().get("id") if r.status_code == 201 else None

        step("3.2 БУХГАЛТЕР создаёт заявку (должен иметь право)")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(fin_tok), json={
            "amount": 8500,
            "planned_date": "2026-07-15",
            "description": "Подписка на бухгалтерский сервис",
        })
        check("Бухгалтер создаёт заявку: 201", r.status_code == 201, r.text[:80])
        fin_req_id = r.json().get("id") if r.status_code == 201 else None

        step("3.3 НАБЛЮДАТЕЛЬ не может создавать заявки → 403")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(view_tok), json={
            "amount": 5000, "planned_date": "2026-07-20", "description": "Тест VIEWER",
        })
        check("VIEWER не создаёт заявки: 403", r.status_code == 403, f"got {r.status_code}")

        step("3.4 НАБЛЮДАТЕЛЬ видит список заявок")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests", headers=h(view_tok))
        check("VIEWER читает список: 200", r.status_code == 200)

        step("3.5 МЕНЕДЖЕР НЕ МОЖЕТ одобрять заявки → 403")
        if mgr_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{mgr_req_id}/status",
                              headers=h(mgr_tok), json={"status": "approved"})
            check("Менеджер не одобряет: 403", r.status_code == 403, f"got {r.status_code}")

        step("3.6 БУХГАЛТЕР одобряет заявку (CanWriteFinance)")
        if mgr_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{mgr_req_id}/status",
                              headers=h(fin_tok), json={"status": "approved"})
            check("Бухгалтер одобряет: 200", r.status_code == 200, f"got {r.status_code} {r.text[:60]}")

        step("3.7 БУХГАЛТЕР удаляет PENDING-заявку (CanWriteFinance)")
        if fin_req_id:
            r = await c.delete(f"/api/v1/companies/{cid}/payment-requests/{fin_req_id}",
                               headers=h(fin_tok))
            check("Бухгалтер удаляет PENDING: 204", r.status_code == 204, f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("4. ЗАЯВКИ — IDOR (межкомпанийная изоляция)")
        # ══════════════════════════════════════════════════════════════════════

        step("4.1 Злоумышленник читает чужие заявки → 403")
        r = await c.get(f"/api/v1/companies/{cid}/payment-requests", headers=h(other_tok))
        check("IDOR: чужой список: 403", r.status_code == 403, f"got {r.status_code}")

        step("4.2 Злоумышленник читает конкретную заявку чужой компании → 404/403")
        if income_req_id:
            r = await c.get(f"/api/v1/companies/{cid}/payment-requests/{income_req_id}", headers=h(other_tok))
            check("IDOR: чужая заявка: 403/404", r.status_code in (403, 404), f"got {r.status_code}")

        step("4.3 Злоумышленник создаёт заявку в чужой компании → 403")
        r = await c.post(f"/api/v1/companies/{cid}/payment-requests", headers=h(other_tok), json={
            "amount": 1, "planned_date": "2026-07-01",
        })
        check("IDOR: создание в чужой: 403", r.status_code == 403, f"got {r.status_code}")

        step("4.4 Злоумышленник одобряет чужую заявку → 403/404")
        if income_req_id:
            r = await c.patch(f"/api/v1/companies/{cid}/payment-requests/{income_req_id}/status",
                              headers=h(other_tok), json={"status": "approved"})
            check("IDOR: одобрение чужой: 403/404", r.status_code in (403, 404), f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("5. ПЛАНОВЫЕ ТРАНЗАКЦИИ — Календарь (CSV-импорт)")
        # ══════════════════════════════════════════════════════════════════════

        step("5.1 Пустой календарь при старте")
        r = await c.get(f"/api/v1/companies/{cid}/planning/calendar", headers=h(owner_tok))
        check("Пустой календарь: 200", r.status_code == 200)
        check("Нет плановых транзакций", r.json() == [], f"got {r.json()}")

        step("5.2 CSV-импорт плановых платежей")
        csv_content = (
            "date,amount,type,description\n"
            f"2026-07-01,45000,EXPENSE,Аренда офиса\n"
            f"2026-07-05,320000,INCOME,Ожидаемый платёж клиента\n"
            f"2026-07-15,28000,EXPENSE,Зарплата курьера\n"
            f"2026-07-20,12000,EXPENSE,Коммунальные услуги\n"
            f"2026-08-01,45000,EXPENSE,Аренда офиса август\n"
            f"2026-08-05,280000,INCOME,Второй транш клиента\n"
        )
        r = await c.post(f"/api/v1/companies/{cid}/planning/import-csv", headers=h(owner_tok),
                         json={"csv_content": csv_content})
        check("CSV-импорт: 201", r.status_code == 201, r.text[:80])
        if r.status_code == 201:
            imported = r.json().get("imported", 0)
            check(f"Импортировано {imported} записей (ожидаем 6)", imported == 6, f"got {imported}")

        step("5.3 Календарь теперь содержит записи")
        r = await c.get(f"/api/v1/companies/{cid}/planning/calendar", headers=h(owner_tok))
        check("Календарь после импорта: 200", r.status_code == 200)
        items = r.json()
        check(f"Все 6 позиций в календаре (got {len(items)})", len(items) == 6, f"count={len(items)}")
        if items:
            item = items[0]
            check("Поле id есть", "id" in item)
            check("Поле plan_date есть", "plan_date" in item)
            check("Поле amount есть", "amount" in item)
            check("Поле type есть", "type" in item)
            check("Поле description есть", "description" in item)

        step("5.4 Суммы корректны")
        if items:
            expenses = [i for i in items if i.get("type") == "EXPENSE"]
            incomes  = [i for i in items if i.get("type") == "INCOME"]
            check(f"4 расхода в календаре (got {len(expenses)})", len(expenses) == 4, f"count={len(expenses)}")
            check(f"2 дохода в календаре (got {len(incomes)})", len(incomes) == 2, f"count={len(incomes)}")

        step("5.5 НАБЛЮДАТЕЛЬ видит календарь")
        r = await c.get(f"/api/v1/companies/{cid}/planning/calendar", headers=h(view_tok))
        check("VIEWER видит календарь: 200", r.status_code == 200)

        step("5.6 НАБЛЮДАТЕЛЬ не может импортировать CSV → 403")
        r = await c.post(f"/api/v1/companies/{cid}/planning/import-csv", headers=h(view_tok),
                         json={"csv_content": "date,amount,type,description\n2026-07-01,100,EXPENSE,Test\n"})
        check("VIEWER не импортирует: 403", r.status_code == 403, f"got {r.status_code}")

        step("5.7 МЕНЕДЖЕР не может импортировать CSV → 403")
        r = await c.post(f"/api/v1/companies/{cid}/planning/import-csv", headers=h(mgr_tok),
                         json={"csv_content": "date,amount,type,description\n2026-07-01,100,EXPENSE,Test\n"})
        check("Менеджер не импортирует: 403", r.status_code == 403, f"got {r.status_code}")

        step("5.8 IDOR: чужой читает чужой календарь → 403")
        r = await c.get(f"/api/v1/companies/{cid}/planning/calendar", headers=h(other_tok))
        check("IDOR: чужой календарь: 403", r.status_code == 403, f"got {r.status_code}")

        step("5.9 Пустой CSV — импорт 0 записей")
        r = await c.post(f"/api/v1/companies/{cid}/planning/import-csv", headers=h(owner_tok),
                         json={"csv_content": "date,amount,type,description\n"})
        check("Пустой CSV: не крашится", r.status_code in (200, 201, 422), f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("6. ЗАЙМЫ — Создание и список")
        # ══════════════════════════════════════════════════════════════════════

        step("6.1 Пустой список займов")
        r = await c.get(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok))
        check("Список займов: 200", r.status_code == 200)
        check("Нет займов изначально", r.json() == [], f"got {r.json()}")

        step("6.2 Создаём аннуитетный кредит на 12 месяцев")
        r = await c.post(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok), json={
            "contract_number": f"KR-{TS}-01",
            "type": "annuity",
            "principal_amount": 1200000,
            "interest_rate": 0.18,
            "start_date": "2026-01-01",
            "duration_months": 12,
        })
        check("Аннуитетный кредит создан: 200/201", r.status_code in (200, 201), r.text[:100])
        loan_annuity_id = r.json().get("id") if r.status_code in (200, 201) else None
        if r.status_code in (200, 201):
            data = r.json()
            check("loan_type = annuity", data.get("loan_type") == "annuity")
            check("total_amount = 1 200 000", float(data.get("total_amount", 0)) == 1200000.0,
                  f"got {data.get('total_amount')}")
            check("is_active = True", data.get("is_active") is True)
            check("term_months = 12", data.get("term_months") == 12)

        step("6.3 Создаём дифференцированный займ на 6 месяцев")
        r = await c.post(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok), json={
            "contract_number": f"ZM-{TS}-01",
            "type": "differentiated",
            "principal_amount": 600000,
            "interest_rate": 0.12,
            "start_date": "2026-03-01",
            "duration_months": 6,
        })
        check("Дифференцированный займ создан", r.status_code in (200, 201), r.text[:100])
        loan_diff_id = r.json().get("id") if r.status_code in (200, 201) else None

        step("6.4 Список займов: теперь 2")
        r = await c.get(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok))
        check("Список займов: 200", r.status_code == 200)
        check("2 займа в списке", len(r.json()) == 2, f"got {len(r.json())}")

        step("6.5 VIEWER может видеть займы")
        r = await c.get(f"/api/v1/companies/{cid}/loans", headers=h(view_tok))
        check("VIEWER видит займы: 200", r.status_code == 200)

        step("6.6 VIEWER не может создавать займы → 403")
        r = await c.post(f"/api/v1/companies/{cid}/loans", headers=h(view_tok), json={
            "contract_number": "KR-VIEWER", "type": "annuity",
            "principal_amount": 100000, "interest_rate": 0.10,
            "start_date": "2026-01-01", "duration_months": 6,
        })
        check("VIEWER не создаёт займы: 403", r.status_code == 403, f"got {r.status_code}")

        step("6.7 МЕНЕДЖЕР не может создавать займы → 403")
        r = await c.post(f"/api/v1/companies/{cid}/loans", headers=h(mgr_tok), json={
            "contract_number": "KR-MGR", "type": "annuity",
            "principal_amount": 100000, "interest_rate": 0.10,
            "start_date": "2026-01-01", "duration_months": 6,
        })
        check("Менеджер не создаёт займы: 403", r.status_code == 403, f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("7. ЗАЙМЫ — График платежей")
        # ══════════════════════════════════════════════════════════════════════

        step("7.1 График аннуитетного кредита: 12 траншей")
        if loan_annuity_id:
            r = await c.get(f"/api/v1/companies/{cid}/loans/{loan_annuity_id}/schedule",
                            headers=h(owner_tok))
            check("График аннуитета: 200", r.status_code == 200, r.text[:80])
            if r.status_code == 200:
                sched = r.json()
                check(f"12 платежей в графике (got {len(sched)})", len(sched) == 12, f"count={len(sched)}")
                # Поля каждого транша
                if sched:
                    t = sched[0]
                    check("Поле payment_date", "payment_date" in t)
                    check("Поле principal_amount", "principal_amount" in t)
                    check("Поле interest_amount",  "interest_amount"  in t)
                    check("Поле total_amount",      "total_amount"     in t)
                    check("Поле is_paid",           "is_paid"          in t)
                    check("Первый транш не оплачен", t["is_paid"] is False)
                    # Проверяем что total = principal + interest
                    for tr in sched:
                        p = float(tr["principal_amount"])
                        i = float(tr["interest_amount"])
                        tot = float(tr["total_amount"])
                        if abs((p + i) - tot) > 0.02:
                            fail(f"total ≠ principal + interest в транше {tr['payment_date']}")
                            break
                    else:
                        ok("total = principal + interest во всех траншах")
                # Даты должны идти вперёд
                if len(sched) >= 2:
                    check("Даты в порядке возрастания",
                          sched[0]["payment_date"] < sched[1]["payment_date"])
                ann_schedule = sched
            else:
                ann_schedule = []
        else:
            ann_schedule = []

        step("7.2 График дифференцированного займа: 6 траншей")
        if loan_diff_id:
            r = await c.get(f"/api/v1/companies/{cid}/loans/{loan_diff_id}/schedule",
                            headers=h(owner_tok))
            check("График дифф: 200", r.status_code == 200, r.text[:80])
            if r.status_code == 200:
                diff_sched = r.json()
                check(f"6 платежей в графике (got {len(diff_sched)})", len(diff_sched) == 6, f"count={len(diff_sched)}")
                if len(diff_sched) >= 2:
                    # При дифференцированном — основной долг одинаков,
                    # % убывает с каждым платежом
                    int1 = float(diff_sched[0]["interest_amount"])
                    int2 = float(diff_sched[1]["interest_amount"])
                    check("Дифф: % убывает со временем", int1 > int2,
                          f"i1={int1} i2={int2}")
            else:
                diff_sched = []
        else:
            diff_sched = []

        step("7.3 VIEWER видит график займа")
        if loan_annuity_id:
            r = await c.get(f"/api/v1/companies/{cid}/loans/{loan_annuity_id}/schedule",
                            headers=h(view_tok))
            check("VIEWER видит график: 200", r.status_code == 200)

        step("7.4 График несуществующего займа → 404")
        fake_loan = "00000000-0000-0000-0000-000000000002"
        r = await c.get(f"/api/v1/companies/{cid}/loans/{fake_loan}/schedule", headers=h(owner_tok))
        check("Несуществующий займ: 404", r.status_code == 404, f"got {r.status_code}")

        step("7.5 IDOR: чужой пробует читать чужой график → 403")
        if loan_annuity_id:
            r = await c.get(f"/api/v1/companies/{cid}/loans/{loan_annuity_id}/schedule",
                            headers=h(other_tok))
            check("IDOR: чужой график: 403", r.status_code == 403, f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("8. ЗАЙМЫ — Оплата траншей")
        # ══════════════════════════════════════════════════════════════════════

        step("8.1 Оплачиваем 1-й транш аннуитетного кредита")
        if ann_schedule:
            s1_id = ann_schedule[0]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s1_id}/pay",
                             headers=h(owner_tok))
            check("Оплата 1-го транша: 200", r.status_code == 200, r.text[:100])
            if r.status_code == 200:
                check("Транш помечен как оплаченный", r.json()["is_paid"] is True)

        step("8.2 Повторная оплата того же транша → ошибка")
        if ann_schedule:
            s1_id = ann_schedule[0]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s1_id}/pay",
                             headers=h(owner_tok))
            check("Повторная оплата: не 200", r.status_code != 200,
                  f"got {r.status_code} — ожидали ошибку")

        step("8.3 Оплачиваем 2-й транш")
        if len(ann_schedule) >= 2:
            s2_id = ann_schedule[1]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s2_id}/pay",
                             headers=h(owner_tok))
            check("Оплата 2-го транша: 200", r.status_code == 200, r.text[:80])

        step("8.4 Оплата транша из другого займа (дифф)")
        if diff_sched:
            d1_id = diff_sched[0]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{d1_id}/pay",
                             headers=h(owner_tok))
            check("Оплата транша дифф: 200", r.status_code == 200, r.text[:80])

        step("8.5 Проверяем график — 2 транша из 12 оплачены")
        if loan_annuity_id:
            r = await c.get(f"/api/v1/companies/{cid}/loans/{loan_annuity_id}/schedule",
                            headers=h(owner_tok))
            if r.status_code == 200:
                paid   = sum(1 for t in r.json() if t["is_paid"])
                unpaid = sum(1 for t in r.json() if not t["is_paid"])
                check(f"2 оплачены, 10 нет (paid={paid}, unpaid={unpaid})",
                      paid == 2 and unpaid == 10)

        step("8.6 VIEWER не может проводить оплату транша → 403")
        if len(ann_schedule) >= 3:
            s3_id = ann_schedule[2]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s3_id}/pay",
                             headers=h(view_tok))
            check("VIEWER не платит транш: 403", r.status_code == 403, f"got {r.status_code}")

        step("8.7 МЕНЕДЖЕР не может проводить оплату → 403")
        if len(ann_schedule) >= 3:
            s3_id = ann_schedule[2]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s3_id}/pay",
                             headers=h(mgr_tok))
            check("Менеджер не платит транш: 403", r.status_code == 403, f"got {r.status_code}")

        step("8.8 Оплата несуществующего транша → 404")
        fake_sched = "00000000-0000-0000-0000-000000000003"
        r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{fake_sched}/pay",
                         headers=h(owner_tok))
        check("Несуществующий транш: 404", r.status_code == 404, f"got {r.status_code}")

        step("8.9 IDOR: чужой оплачивает чужой транш → 404/403")
        if ann_schedule and len(ann_schedule) >= 3:
            s3_id = ann_schedule[2]["id"]
            r = await c.post(f"/api/v1/companies/{cid}/loans/schedule/{s3_id}/pay",
                             headers=h(other_tok))
            check("IDOR: оплата чужого транша: 403/404", r.status_code in (403, 404),
                  f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        section("9. ЗАЙМЫ — P&L отражает платежи по займам")
        # ══════════════════════════════════════════════════════════════════════

        step("9.1 Оплаченные транши создают транзакции расхода")
        r = await c.get(f"/api/v1/companies/{cid}/reports/pl"
                        "?start_date=2026-01-01&end_date=2026-12-31",
                        headers=h(owner_tok))
        check("P&L загрузился", r.status_code == 200)
        if r.status_code == 200:
            pl = r.json()
            # Оплата первых 2 траншей аннуитетного + 1 дифференц. = расходы в отчёте
            expenses = pl.get("opex", 0)
            check("Расходы отражают оплаченные транши", expenses > 0, f"opex={expenses}")
            print(f"      P&L — Выручка: {pl.get('revenue',0):,.0f} ₽  |  "
                  f"Расходы (с траншами): {expenses:,.0f} ₽")


        # ══════════════════════════════════════════════════════════════════════
        section("10. ЗАЙМЫ — Удаление")
        # ══════════════════════════════════════════════════════════════════════

        step("10.1 Создаём займ для удаления")
        r = await c.post(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok), json={
            "contract_number": f"DEL-{TS}", "type": "annuity",
            "principal_amount": 50000, "interest_rate": 0.15,
            "start_date": "2026-06-01", "duration_months": 3,
        })
        check("Займ для удаления создан", r.status_code in (200, 201))
        del_loan_id = r.json().get("id") if r.status_code in (200, 201) else None

        step("10.2 VIEWER не может удалять займы → 403")
        if del_loan_id:
            r = await c.delete(f"/api/v1/companies/{cid}/loans/{del_loan_id}", headers=h(view_tok))
            check("VIEWER не удаляет займ: 403", r.status_code == 403, f"got {r.status_code}")

        step("10.3 Удаляем займ (OWNER)")
        if del_loan_id:
            r = await c.delete(f"/api/v1/companies/{cid}/loans/{del_loan_id}", headers=h(owner_tok))
            check("Удаление займа: 204", r.status_code == 204, f"got {r.status_code}")

        step("10.4 Удалённый займ больше не в списке")
        r = await c.get(f"/api/v1/companies/{cid}/loans", headers=h(owner_tok))
        ids = [l["id"] for l in r.json()]
        check("Удалённого займа нет в списке", del_loan_id not in ids if del_loan_id else True)

        step("10.5 Удаление несуществующего → 404")
        r = await c.delete(f"/api/v1/companies/{cid}/loans/{fake_loan}", headers=h(owner_tok))
        check("Удаление несуществующего: 404", r.status_code == 404, f"got {r.status_code}")

        step("10.6 IDOR: чужой удаляет чужой займ → 403/404")
        if loan_diff_id:
            r = await c.delete(f"/api/v1/companies/{cid}/loans/{loan_diff_id}", headers=h(other_tok))
            check("IDOR: удаление чужого займа: 403/404", r.status_code in (403, 404),
                  f"got {r.status_code}")


        # ══════════════════════════════════════════════════════════════════════
        # ИТОГ
        # ══════════════════════════════════════════════════════════════════════
        total = PASS + FAIL
        print(f"\n{'═'*62}")
        print(f"  ПЛАТЁЖНЫЙ КАЛЕНДАРЬ: {total} проверок")
        print(f"  \033[32mPASS: {PASS}\033[0m   \033[31mFAIL: {FAIL}\033[0m")
        if FAILURES:
            print("\n  Проблемы:")
            for name, detail in FAILURES:
                print(f"    ✗ {name}")
                if detail:
                    print(f"      {detail[:100]}")
        print(f"{'═'*62}\n")


asyncio.run(run())
