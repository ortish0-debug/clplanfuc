-- ============================================================================
-- Тестовые данные для интеграционного тестирования
-- ============================================================================

-- Получить ID компании (используем существующую)
-- Предполагаем что уже есть компания с ID
-- SELECT id FROM companies LIMIT 1;

-- Используем известный ID компании из скриншота
-- a49eff66-4d42-406b-9c41-0094fe1ecadc

DO $$
DECLARE
    company_id UUID := 'a49eff66-4d42-406b-9c41-0094fe1ecadc'::UUID;
    user_id UUID;
    cat_revenue UUID;
    cat_expense UUID;
    cat_salary UUID;
    acc_cash UUID;
    acc_debt UUID;
BEGIN

    -- ========================================================================
    -- 1. ОПЕРАЦИИ (Transactions) - для тестирования фильтров и парсера
    -- ========================================================================

    INSERT INTO transactions (id, company_id, type, amount, description, "date", status, created_at, updated_at, ai_classified)
    VALUES
        (gen_random_uuid(), company_id, 'INCOME'::transaction_type, 500000, 'Продажа товаров', '2026-06-01'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false),
        (gen_random_uuid(), company_id, 'EXPENSE'::transaction_type, 150000, 'Зарплата сотрудникам', '2026-06-02'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false),
        (gen_random_uuid(), company_id, 'INCOME'::transaction_type, 250000, 'Услуги консультирования', '2026-06-05'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false),
        (gen_random_uuid(), company_id, 'EXPENSE'::transaction_type, 50000, 'Аренда офиса', '2026-06-03'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false),
        (gen_random_uuid(), company_id, 'EXPENSE'::transaction_type, 30000, 'Интернет и связь', '2026-06-04'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false),
        (gen_random_uuid(), company_id, 'INCOME'::transaction_type, 100000, 'Продажа услуг', '2026-06-10'::date, 'CONFIRMED'::transaction_status, NOW(), NOW(), false)
    ON CONFLICT DO NOTHING;

    -- ========================================================================
    -- 2. БЮДЖЕТЫ (Budgets) - для редактирования плана
    -- ========================================================================

    -- Сначала убедимся что есть категории доходов и расходов
    SELECT id INTO cat_revenue FROM categories
    WHERE company_id = company_id AND code = '4000' LIMIT 1;

    IF cat_revenue IS NULL THEN
        INSERT INTO categories (id, company_id, name, code, type, is_system, created_at)
        VALUES (gen_random_uuid(), company_id, 'Доходы', '4000', 'INCOME', true, NOW())
        RETURNING id INTO cat_revenue;
    END IF;

    SELECT id INTO cat_expense FROM categories
    WHERE company_id = company_id AND code = '5000' LIMIT 1;

    IF cat_expense IS NULL THEN
        INSERT INTO categories (id, company_id, name, code, type, is_system, created_at)
        VALUES (gen_random_uuid(), company_id, 'Расходы', '5000', 'EXPENSE', true, NOW())
        RETURNING id INTO cat_expense;
    END IF;

    SELECT id INTO cat_salary FROM categories
    WHERE company_id = company_id AND code = '5100' LIMIT 1;

    IF cat_salary IS NULL THEN
        INSERT INTO categories (id, company_id, name, code, type, is_system, parent_id, created_at)
        VALUES (gen_random_uuid(), company_id, 'Зарплата', '5100', 'EXPENSE', true, cat_expense, NOW())
        RETURNING id INTO cat_salary;
    END IF;

    -- Добавляем бюджеты
    INSERT INTO budgets (id, company_id, category_id, year, month, plan_amount, created_at, updated_at)
    VALUES
        (gen_random_uuid(), company_id, cat_revenue, 2026, 6, 1000000, NOW(), NOW()),
        (gen_random_uuid(), company_id, cat_expense, 2026, 6, 400000, NOW(), NOW()),
        (gen_random_uuid(), company_id, cat_salary, 2026, 6, 200000, NOW(), NOW())
    ON CONFLICT (company_id, category_id, year, month, project_id) DO UPDATE
    SET plan_amount = EXCLUDED.plan_amount, updated_at = NOW();

    -- ========================================================================
    -- 3. СЧЕТА (Accounts) - для отчётов Balance
    -- ========================================================================

    SELECT id INTO acc_cash FROM accounts
    WHERE company_id = company_id AND code = '1010' LIMIT 1;

    IF acc_cash IS NULL THEN
        INSERT INTO accounts (id, company_id, code, name, type, is_system, is_active, created_at)
        VALUES (gen_random_uuid(), company_id, '1010', 'Касса', 'ASSET', true, true, NOW())
        RETURNING id INTO acc_cash;
    END IF;

    SELECT id INTO acc_debt FROM accounts
    WHERE company_id = company_id AND code = '6010' LIMIT 1;

    IF acc_debt IS NULL THEN
        INSERT INTO accounts (id, company_id, code, name, type, is_system, is_active, created_at)
        VALUES (gen_random_uuid(), company_id, '6010', 'Счетная позиция', 'LIABILITY', true, true, NOW())
        RETURNING id INTO acc_debt;
    END IF;

    -- ========================================================================
    -- 4. Логирование
    -- ========================================================================

    RAISE NOTICE 'Тестовые данные успешно добавлены для компании %', company_id;

END $$;
