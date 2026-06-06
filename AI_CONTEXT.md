# AI_CONTEXT — Next-Gen PlanFact

## Стек
- Backend: FastAPI + SQLAlchemy async + PostgreSQL + Alembic
- Frontend: React 18 (CDN, без сборки) + Babel + axios + Chart.js
- Auth: JWT (access_token) + bcrypt + опционально TOTP
- Файлы: app/main.py (бэкенд), static/index.html (фронтенд)

## Структура бэкенда
- app/main.py — точка входа, 48 роутеров
- app/infrastructure/api/v1/routers/ — все роутеры
- app/domain/models/ — SQLAlchemy модели
- app/services/ — бизнес-логика
- app/infrastructure/database/session.py — AsyncSession

## Ключевые роутеры
- auth.router — /auth/token (form-data), /auth/register (JSON)
- crud_operations.router — /accounts, /categories, /transactions (PostgreSQL async)
- reports.router — /reports/pl, /reports/cashflow, /reports/balance (требуют start_date, end_date как query params в формате YYYY-MM-DD)
- counterparties.router — /counterparties, /counterparties/debts
- projects.router — /projects
- budgets.router — /budgets/plan-fact, /budgets/save
- planning.router — /planning/calendar
- loans.router — /loans, /loans/{id}/schedule
- payroll.router — /employees, /payroll
- assets.router — /assets, /assets/amortize

## Модели БД (важные поля)
- Transaction: id, company_id, account_id, category_id, transaction_type (INCOME/EXPENSE), amount (Decimal), description, payment_date (date), status, is_deleted
- Account: id, company_id, name, account_type (CHECKING/SAVINGS/CASH/CREDIT), currency, current_balance, is_deleted
- Category: id, company_id, name, category_type (INCOME/EXPENSE), parent_id, is_system, is_deleted
- User: id, email, hashed_password, full_name, is_active
- Company: id, name, inn, currency, is_deleted

## API форматы
- Авторизация: POST /auth/token с form-data (username, password) → {access_token, company_id, email, role}
- Все защищённые запросы: заголовок Authorization: Bearer {token}
- Транзакции приходят в поле value (не transactions): res.data.value
- transaction_type приходит в нижнем регистре: 'income' / 'expense'
- Даты: payment_date в формате YYYY-MM-DD

## Тестовый пользователь
- Email: demo@example.com
- Password: demo12345
- Company ID: eb05bd7b-59c7-4f18-a237-c0f236008ba9

## Запуск
```
cd c:\Users\thero\Desktop\planfa
$env:PYTHONIOENCODING = "utf-8"; python -m app.main
```
Сервер: http://localhost:8000
Фронтенд: http://localhost:8000/static/index.html
API Docs: http://localhost:8000/api/docs
Все роуты: /api/v1/... (например /api/v1/auth/register, /api/v1/auth/token)

## Известные особенности
- Кириллица отображается как ???? в PowerShell на Windows — в браузере данные отображаются правильно если сервер запущен с PYTHONIOENCODING=utf-8
- Windows локаль cp1251 — всегда запускать сервер через: `set PYTHONIOENCODING=utf-8 && python -m app.main`
- rate_limiter для /auth/ установлен на 100 req/min (увеличен для разработки)

## Правила разработки
- Не создавать новые файлы без необходимости
- Все правки фронтенда — только в static/index.html
- Все правки бэкенда — в существующих роутерах в app/infrastructure/api/v1/routers/
- Не менять app/domain/models/ без крайней необходимости
- После каждой правки показывать размер файла static/index.html чтобы подтвердить изменение
- Тестировать через python requests скрипты, не через PowerShell heredoc
