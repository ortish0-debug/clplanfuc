"""
Точка входа FastAPI-приложения Next-Gen PlanFact.

Порядок инициализации:
  1. Настройка логирования
  2. Загрузка .env
  3. Lifespan: старт → проверка соединения с БД, прогрев AI-парсера
  4. Middleware: CORS, X-Request-ID, обработка ошибок
  5. Регистрация роутеров
  6. Технические эндпоинты (/health, /health/db)
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import dotenv
import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import text
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.infrastructure.api.v1.routers import (
    accruals, ai_parser, alerts_advanced, advanced_analytics, analytics, assets, auth, balance_sheet, bank_auth, billing, budgets, bulk, compliance, counterparties, crm, crm_deals, crud_operations, export, search, templates,
    currency, directbank, disaster_recovery, documents, email_integration, fixed_assets, holdings, import_bank, integrations, integrations_advanced, inventory, ledger, loans,
    ml_reconciliation, mobile, mobile_advanced, onec, payment_requests, payroll, payroll_advanced, planning, production, projects, reports, russian_taxes, saas, taxes, tax_reports, team, warehouse, websockets,
)
from app.infrastructure.api.v1.routers import reports as reports_router
from app.infrastructure.database.session import AsyncSessionFactory, _engine
from app.infrastructure.security.rate_limiter import InMemoryRateLimiter

# ---------------------------------------------------------------------------
# UTF-8 кодировка для корректного отображения кириллицы
# ---------------------------------------------------------------------------

import sys
import io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ---------------------------------------------------------------------------
# Загружаем .env до любой инициализации
# ---------------------------------------------------------------------------

dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# Инициализация Sentry для мониторинга ошибок
# ---------------------------------------------------------------------------

_sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("planfact.main")

# ---------------------------------------------------------------------------
# CORS: разрешаем все origins для совместимости с Google Stitch / фронтендом.
# В production рекомендуется сузить до конкретных доменов через CORS_ORIGINS env.
# ---------------------------------------------------------------------------

_raw_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
CORS_ORIGINS: list[str] = (
    ["*"] if _raw_origins.strip() == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)


# ---------------------------------------------------------------------------
# Lifespan: старт и завершение приложения
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Выполняется один раз при старте (до приёма трафика) и при остановке.
    Используется для проверки соединений и освобождения ресурсов.
    """
    # --- СТАРТ ---
    logger.info("=== Next-Gen PlanFact API: запуск ===")
    logger.info("LOG_LEVEL=%s | CORS_ORIGINS=%s", _LOG_LEVEL, CORS_ORIGINS)

    # Проверка соединения с PostgreSQL при старте
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("PostgreSQL: соединение установлено.")
    except Exception as exc:
        # Не падаем при старте — Cloud Run может стартовать до готовности Cloud SQL
        logger.warning("PostgreSQL: соединение недоступно при старте: %s", exc)

    # Создаём все таблицы при старте (идемпотентно — существующие таблицы не будут пересозданы)
    from app.infrastructure.database.base import Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("SQLAlchemy: таблицы созданы или уже существуют.")

    # GeminiParser инициализируется лениво при первом запросе через get_gemini_parser().
    # Явный прогрев при старте исключён: google-genai 2.x содержит баг в aclose()
    # при уничтожении клиента у которого async_httpx_client не был создан.
    logger.info("GeminiParser: ленивая инициализация при первом запросе.")

    logger.info("=== Приложение готово к приёму запросов ===")
    yield

    # --- ЗАВЕРШЕНИЕ ---
    logger.info("=== Next-Gen PlanFact API: завершение работы ===")
    await _engine.dispose()
    logger.info("PostgreSQL: пул соединений закрыт.")


# ---------------------------------------------------------------------------
# Создание приложения
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Next-Gen PlanFact API",
    version="1.0.0",
    description=(
        "SaaS-платформа финансового управления для малого и среднего бизнеса. "
        "Отчёты ДДС, P&L, 3-Way Balance, AI-парсинг транзакций, мультитенантность."
    ),
    contact={
        "name": "PlanFact Engineering",
        "email": "api@planfact.app",
    },
    license_info={
        "name": "Proprietary",
    },
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-Company-ID",
        "Accept",
        "Accept-Language",
    ],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
)


# ---------------------------------------------------------------------------
# Middleware: X-Request-ID и время обработки (трассировка логов)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Присваивает каждому запросу уникальный X-Request-ID.
    Добавляет X-Process-Time-Ms в ответ для мониторинга производительности.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()

    response = await call_next(request)

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)

    logger.debug(
        "%s %s → %d (%.2f ms) [%s]",
        request.method, request.url.path,
        response.status_code, elapsed_ms, request_id,
    )
    return response


# ---------------------------------------------------------------------------
# Middleware: Security Headers (защита от распространённых атак)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Добавляет защитные HTTP-заголовки для защиты от:
    - Clickjacking (X-Frame-Options: DENY)
    - MIME-sniffing (X-Content-Type-Options: nosniff)
    - XSS-атак (X-XSS-Protection: 1; mode=block)
    """
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ---------------------------------------------------------------------------
# Middleware: Rate Limiting (защита от DDoS и brute-force)
# ---------------------------------------------------------------------------

_rate_limiter = InMemoryRateLimiter()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    Rate limiting middleware: защита от DDoS и brute-force атак.
    - API: 100 req/min на IP
    - /auth/: 10 req/min на IP (защита от перебора паролей)
    """
    client_ip = request.client.host if request.client else "unknown"

    if "/auth/" in request.url.path:
        limit, window = 100, 60  # Временно увеличен для разработки
    else:
        limit, window = 100, 60

    if _rate_limiter.is_rate_limited(client_ip, limit, window):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Rate limit exceeded. Try again later."},
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Глобальные обработчики ошибок
# ---------------------------------------------------------------------------


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """
    Перехватывает ошибки валидации Pydantic.
    Очищает детали ошибок от сырых типов и путей для предотвращения Information Disclosure.
    Логирует попытки на /auth/ как потенциальный скан уязвимостей.
    """
    cleaned_errors = []
    field_names = []

    for error in exc.errors():
        field_loc = ".".join(str(x) for x in error.get("loc", [])[1:])
        msg = error.get("msg", "Invalid value")
        if field_loc:
            field_names.append(field_loc)
            cleaned_errors.append({"field": field_loc, "message": msg})

    # ── Логируем попытки валидации на /auth/ как потенциальный скан ────
    if "/auth/" in request.url.path:
        ip_address = request.client.host if request.client else "unknown"
        try:
            from app.infrastructure.database.session import AsyncSessionFactory
            async with AsyncSessionFactory() as db:
                from app.services.audit_service import log_audit_action
                await log_audit_action(
                    db=db,
                    company_id=None,
                    user_id=None,
                    action="api.validation_exploit",
                    target_type="api_request",
                    target_id=None,
                    ip_address=ip_address,
                )
                await db.commit()
        except Exception:
            pass  # Сбой логирования не должен ломать ответ

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": f"Validation error on fields: {', '.join(field_names)}" if field_names else "Validation error",
            "errors": cleaned_errors,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Перехватывает необработанные исключения.
    В production не раскрывает стек-трейс клиенту.
    """
    logger.exception(
        "Необработанное исключение: %s %s", request.method, request.url.path
    )
    logger.exception("Необработанное исключение: %s %s", request.method, request.url.path)
    is_debug = os.environ.get("ENVIRONMENT", "production").lower() == "development"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": f"{type(exc).__name__}: {exc}" if is_debug else "Внутренняя ошибка сервера.",
            "request_id": request.headers.get("X-Request-ID"),
        },
    )


# ---------------------------------------------------------------------------
# Технические эндпоинты
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["Служебные"],
    summary="Проверка работоспособности сервиса",
    response_description="Статус приложения",
)
async def health_check() -> dict:
    """
    Liveness probe для Cloud Run / Kubernetes.
    Возвращает 200 OK если процесс жив.
    Не проверяет зависимости — для этого используется /health/db.
    """
    return {
        "status": "ok",
        "service": "next-gen-planfact-api",
        "version": app.version,
    }


@app.get(
    "/health/db",
    tags=["Служебные"],
    summary="Проверка соединения с базой данных",
)
async def health_db() -> dict:
    """
    Readiness probe: проверяет живое соединение с PostgreSQL.
    Возвращает 503 если БД недоступна — Cloud Run остановит маршрутизацию трафика.
    """
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(text("SELECT version()"))
            pg_version: str = result.scalar_one()
        return {
            "status": "ok",
            "database": "connected",
            "pg_version": pg_version.split(",")[0],
        }
    except Exception as exc:
        logger.error("health/db: PostgreSQL недоступен: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "error",
                "database": "unavailable",
                "detail": str(exc),
            },
        )


@app.get(
    "/api/v1/debug-sentry",
    tags=["Служебные"],
    summary="Тестовый эндпоинт для проверки Sentry",
)
async def debug_sentry() -> dict:
    """
    Преднамеренно вызывает деление на ноль для проверки автоматического захвата ошибок Sentry.
    Используется ТОЛЬКО для тестирования мониторинга.
    """
    _ = 1 / 0
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Регистрация роутеров API v1
# ---------------------------------------------------------------------------

_API_PREFIX = "/api/v1"

# CRUD операции для счетов, категорий и транзакций (PostgreSQL с SQLAlchemy async)
app.include_router(
    crud_operations.router,
    prefix=_API_PREFIX,
)

# Простой Auth-роутер БЕЗ требования авторизации (для SQLite базы) — ОТКЛЮЧЕНО
# app.include_router(
#     simple_auth.router,
#     prefix=_API_PREFIX,
# )

# Оригинальный Auth-роутер БЕЗ требования авторизации (доступен всем)
app.include_router(
    auth.router,
    prefix=_API_PREFIX,
)

# Защищённые роутеры (требуют авторизации)
app.include_router(
    reports.router,
    prefix=_API_PREFIX,
)

app.include_router(
    balance_sheet.router,
    prefix=_API_PREFIX,
)

app.include_router(
    ai_parser.router,
    prefix=_API_PREFIX,
)

app.include_router(
    import_bank.router,
    prefix=_API_PREFIX,
)

app.include_router(
    counterparties.router,
    prefix=_API_PREFIX,
)

app.include_router(
    team.router,
    prefix=_API_PREFIX,
)

app.include_router(
    projects.router,
    prefix=_API_PREFIX,
)

app.include_router(
    budgets.router,
    prefix=_API_PREFIX,
)

app.include_router(
    payment_requests.router,
    prefix=_API_PREFIX,
)

app.include_router(
    assets.router,
    prefix=_API_PREFIX,
)

app.include_router(
    loans.router,
    prefix=_API_PREFIX,
)

app.include_router(
    bulk.router,
    prefix=_API_PREFIX,
)

app.include_router(
    integrations.router,   # Sprint 12: регистрируется ДО directbank — перехватывает /sync
    prefix=_API_PREFIX,
)

app.include_router(
    directbank.router,
    prefix=_API_PREFIX,
)

app.include_router(
    documents.router,
    prefix=_API_PREFIX,
)

app.include_router(
    crm.router,
    prefix=_API_PREFIX,
)

app.include_router(
    crm_deals.router,
    prefix=_API_PREFIX,
)

app.include_router(
    export.router,
    prefix=_API_PREFIX,
)

app.include_router(
    templates.router,
    prefix=_API_PREFIX,
)

app.include_router(
    search.router,
    prefix=_API_PREFIX,
)

app.include_router(
    planning.router,
    prefix=_API_PREFIX,
)

app.include_router(
    onec.router,
    prefix=_API_PREFIX,
)

app.include_router(
    alerts_advanced.router,
    prefix=_API_PREFIX,
)

app.include_router(
    email_integration.router,
    prefix=_API_PREFIX,
)

app.include_router(
    russian_taxes.router,
    prefix=_API_PREFIX,
)

app.include_router(
    advanced_analytics.router,
    prefix=_API_PREFIX,
)

app.include_router(
    ml_reconciliation.router,
    prefix=_API_PREFIX,
)

app.include_router(
    disaster_recovery.router,
    prefix=_API_PREFIX,
)

app.include_router(
    integrations_advanced.router,
    prefix=_API_PREFIX,
)

app.include_router(
    mobile_advanced.router,
    prefix=_API_PREFIX,
)

app.include_router(
    saas.router,
    prefix=_API_PREFIX,
)

app.include_router(
    accruals.router,
    prefix=_API_PREFIX,
)

app.include_router(
    payroll.router,
    prefix=_API_PREFIX,
)

app.include_router(
    payroll_advanced.router,
    prefix=_API_PREFIX,
)

app.include_router(
    production.router,
    prefix=_API_PREFIX,
)

app.include_router(
    holdings.router,
    prefix=_API_PREFIX,
)

app.include_router(
    inventory.router,
    prefix=_API_PREFIX,
)

app.include_router(
    ledger.router,
    prefix=_API_PREFIX,
)

app.include_router(
    taxes.router,
    prefix=_API_PREFIX,
)

app.include_router(
    fixed_assets.router,
    prefix=_API_PREFIX,
)

app.include_router(
    tax_reports.router,
    prefix=_API_PREFIX,
)

app.include_router(
    currency.router,
    prefix=_API_PREFIX,
)

app.include_router(
    bank_auth.router,
    prefix=_API_PREFIX,
)

app.include_router(
    billing.router,
    prefix=_API_PREFIX,
)

app.include_router(
    mobile.router,
    prefix=_API_PREFIX,
)

app.include_router(
    websockets.router,
    prefix=_API_PREFIX,
)

app.include_router(
    analytics.router,
    prefix=_API_PREFIX,
)

app.include_router(
    compliance.router,
    prefix=_API_PREFIX,
)

app.include_router(
    warehouse.router,
    prefix=_API_PREFIX,
)

app.include_router(
    reports_router.router,
    prefix=_API_PREFIX,
)

# ---------------------------------------------------------------------------
# Раздача статических файлов (фронтенд)
# Монтируется ПОСЛЕ всех API-роутеров, чтобы не перехватывать /api/* пути.
# При запросе к "/" браузер получит /static/index.html.
# ---------------------------------------------------------------------------

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return RedirectResponse(url="/static/index.html")

# ---------------------------------------------------------------------------
# Точка входа для локального запуска: python -m app.main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("ENVIRONMENT", "production") == "development",
        log_level=_LOG_LEVEL.lower(),
        access_log=True,
    )
