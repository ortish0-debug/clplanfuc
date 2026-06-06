"""
FastAPI роутер: импорт банковских выписок + CRUD правил автокатегоризации.

Эндпоинты:
  POST   /companies/{id}/import/1c              — парсинг файла, предпросмотр
  POST   /companies/{id}/import/1c/confirm      — сохранение подтверждённых транзакций
  GET    /companies/{id}/import/rules           — список правил
  POST   /companies/{id}/import/rules           — создать правило
  PATCH  /companies/{id}/import/rules/{rule_id} — обновить правило
  DELETE /companies/{id}/import/rules/{rule_id} — удалить правило
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType
from app.domain.models.rules import AutoRule, MatchField, MatchType
from app.domain.schemas.import_bank import (
    AutoRuleCreate,
    AutoRuleResponse,
    AutoRuleUpdate,
    BankStatementPreviewResponse,
    ConfirmImportRequest,
    ConfirmImportResponse,
    ParsedTransactionPreview,
)
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.services.bank_importer import parse_1c_statement
from app.services.currency_service import convert_to_rub, get_rate
from app.services.notification_service import send_telegram_alert

router = APIRouter(
    prefix="/companies/{company_id}/import",
    tags=["Импорт выписок"],
)

_MAX_FILE_SIZE_MB = 10
_MAX_FILE_BYTES   = _MAX_FILE_SIZE_MB * 1_024 * 1_024


# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _require_account(
    db: AsyncSession,
    account_id: UUID,
    company_id: UUID,
) -> Account:
    """Проверяет IDOR и возвращает счёт."""
    result = await db.execute(
        select(Account).where(
            and_(
                Account.id         == account_id,
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
    )
    acc = result.scalar_one_or_none()
    if acc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Счёт {account_id} не найден в компании {company_id}.",
        )
    return acc


async def _require_rule(
    db: AsyncSession,
    rule_id: UUID,
    company_id: UUID,
) -> AutoRule:
    result = await db.execute(
        select(AutoRule).where(
            and_(AutoRule.id == rule_id, AutoRule.company_id == company_id)
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Правило {rule_id} не найдено.",
        )
    return rule


def _map_txn_type(raw: str) -> TransactionType:
    return TransactionType.INCOME if raw == "income" else TransactionType.EXPENSE


# ─────────────────────────────────────────────────────────────────────────────
# ИМПОРТ — ПРЕДПРОСМОТР
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/1c",
    response_model=BankStatementPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Парсинг выписки 1С Клиент-Банк — предпросмотр",
    description=(
        "Принимает файл выписки (.txt, кодировка Windows-1251 или UTF-8). "
        "Возвращает список распознанных транзакций с предложенными категориями. "
        "Данные **не сохраняются** — для сохранения вызовите /import/1c/confirm."
    ),
)
async def preview_1c_import(
    company_id: UUID,
    file: UploadFile = File(..., description="Файл выписки 1С (.txt)"),
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> BankStatementPreviewResponse:
    # ── Проверка размера ────────────────────────────────────────────────────
    raw = await file.read()
    if len(raw) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл превышает {_MAX_FILE_SIZE_MB} МБ.",
        )
    if len(raw) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Загруженный файл пустой.",
        )

    # ── Парсинг ─────────────────────────────────────────────────────────────
    try:
        statement = await parse_1c_statement(raw, company_id, db)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ошибка разбора файла: {exc}",
        ) from exc

    if not statement.transactions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Файл не содержит транзакций. "
                "Убедитесь, что формат соответствует 1С Клиент-Банк."
            ),
        )

    rules_applied = sum(1 for t in statement.transactions if t.suggested_category_id)

    previews = [
        ParsedTransactionPreview(
            doc_number              = t.doc_number,
            payment_date            = t.payment_date,
            amount                  = t.amount,
            transaction_type        = t.transaction_type,
            counterparty_name       = t.counterparty_name,
            counterparty_inn        = t.counterparty_inn,
            counterparty_account    = t.counterparty_account,
            description             = t.description,
            suggested_category_id   = t.suggested_category_id,
            suggested_category_name = t.suggested_category_name,
            rule_name               = t.rule_name,
        )
        for t in statement.transactions
    ]

    return BankStatementPreviewResponse(
        our_account     = statement.our_account,
        bank_name       = statement.bank_name,
        period_from     = statement.period_from,
        period_to       = statement.period_to,
        opening_balance = statement.opening_balance,
        closing_balance = statement.closing_balance,
        total_count     = len(statement.transactions),
        income_count    = statement.income_count,
        expense_count   = statement.expense_count,
        total_income    = statement.total_income,
        total_expense   = statement.total_expense,
        rules_applied   = rules_applied,
        transactions    = previews,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ИМПОРТ — ПОДТВЕРЖДЕНИЕ И СОХРАНЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/1c/confirm",
    response_model=ConfirmImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Сохранить импортированные транзакции в БД",
)
async def confirm_1c_import(
    company_id: UUID,
    body: ConfirmImportRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> ConfirmImportResponse:
    account = await _require_account(db, body.account_id, company_id)

    # Собираем уже существующие bank_transaction_id для дедупликации
    ext_ids = {t.bank_transaction_id for t in body.transactions if t.bank_transaction_id}
    existing_ext_ids: set[str] = set()
    if ext_ids:
        dup_result = await db.execute(
            select(Transaction.bank_transaction_id).where(
                and_(
                    Transaction.account_id == account.id,
                    Transaction.bank_transaction_id.in_(ext_ids),
                )
            )
        )
        existing_ext_ids = {row[0] for row in dup_result.fetchall()}

    imported:      list[Transaction] = []
    skipped_count: int               = 0
    total_income   = Decimal("0.00")
    total_expense  = Decimal("0.00")

    for item in body.transactions:
        # Пропускаем дубликаты
        if item.bank_transaction_id and item.bank_transaction_id in existing_ext_ids:
            skipped_count += 1
            continue

        txn_type = _map_txn_type(item.transaction_type)
        pay_date = item.payment_date

        # Multi-currency conversion: convert to RUB for ledger
        rub_amount = await convert_to_rub(
            db,
            float(item.amount),
            account.currency,
            pay_date,
        )

        # Calculate exchange rate
        exchange_rate = (
            Decimal(str(rub_amount / float(item.amount)))
            if item.amount != 0
            else Decimal("1.000000")
        )

        txn = Transaction(
            id                   = uuid.uuid4(),
            company_id           = company_id,
            account_id           = account.id,
            category_id          = item.category_id,
            created_by_user_id   = current_user.user_id,
            transaction_type     = txn_type,
            status               = TransactionStatus.CONFIRMED,
            amount               = item.amount,
            currency             = account.currency,
            exchange_rate        = exchange_rate,
            amount_base_currency = Decimal(str(rub_amount)),
            payment_date         = pay_date,
            accrual_date         = item.accrual_date or pay_date,
            description          = item.description,
            counterparty         = item.counterparty,
            bank_transaction_id  = item.bank_transaction_id,
            ai_classified        = False,
            tags                 = ["imported_1c"],
            meta                 = {
                "imported_at":    datetime.now(tz=timezone.utc).isoformat(),
                "counterparty_inn": item.counterparty_inn,
            },
            is_deleted           = False,
        )
        db.add(txn)
        imported.append(txn)

        # Обновляем денормализованный баланс счёта
        if txn_type == TransactionType.INCOME:
            account.current_balance += item.amount
            total_income            += item.amount
        else:
            account.current_balance -= item.amount
            total_expense           += item.amount

    await db.flush()

    return ConfirmImportResponse(
        imported_count = len(imported),
        skipped_count  = skipped_count,
        total_income   = total_income,
        total_expense  = total_expense,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AUTO RULES — CRUD
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/rules",
    response_model=list[AutoRuleResponse],
    summary="Список правил автокатегоризации",
)
async def list_rules(
    company_id: UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> list[AutoRuleResponse]:
    result = await db.execute(
        select(AutoRule)
        .where(AutoRule.company_id == company_id)
        .order_by(AutoRule.priority.desc(), AutoRule.created_at)
    )
    rules = result.scalars().all()
    return [
        AutoRuleResponse(
            id                    = r.id,
            company_id            = r.company_id,
            name                  = r.name,
            field_to_match        = r.field_to_match.value,
            match_type            = r.match_type.value,
            pattern               = r.pattern,
            suggested_category_id = r.suggested_category_id,
            priority              = r.priority,
            is_active             = r.is_active,
        )
        for r in rules
    ]


@router.post(
    "/rules",
    response_model=AutoRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать правило автокатегоризации",
)
async def create_rule(
    company_id: UUID,
    body: AutoRuleCreate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> AutoRuleResponse:
    try:
        field  = MatchField(body.field_to_match)
        mtype  = MatchType(body.match_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rule = AutoRule(
        id                    = uuid.uuid4(),
        company_id            = company_id,
        name                  = body.name,
        field_to_match        = field,
        match_type            = mtype,
        pattern               = body.pattern,
        suggested_category_id = body.suggested_category_id,
        priority              = body.priority,
        is_active             = body.is_active,
    )
    db.add(rule)
    await db.flush()

    return AutoRuleResponse(
        id=rule.id, company_id=rule.company_id, name=rule.name,
        field_to_match=rule.field_to_match.value, match_type=rule.match_type.value,
        pattern=rule.pattern, suggested_category_id=rule.suggested_category_id,
        priority=rule.priority, is_active=rule.is_active,
    )


@router.patch(
    "/rules/{rule_id}",
    response_model=AutoRuleResponse,
    summary="Обновить правило",
)
async def update_rule(
    company_id: UUID,
    rule_id:    UUID,
    body:       AutoRuleUpdate,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> AutoRuleResponse:
    rule = await _require_rule(db, rule_id, company_id)

    if body.name                  is not None: rule.name                  = body.name
    if body.pattern               is not None: rule.pattern               = body.pattern
    if body.suggested_category_id is not None: rule.suggested_category_id = body.suggested_category_id
    if body.priority              is not None: rule.priority              = body.priority
    if body.is_active             is not None: rule.is_active             = body.is_active
    if body.field_to_match        is not None:
        try:    rule.field_to_match = MatchField(body.field_to_match)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    if body.match_type            is not None:
        try:    rule.match_type    = MatchType(body.match_type)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    await db.flush()
    return AutoRuleResponse(
        id=rule.id, company_id=rule.company_id, name=rule.name,
        field_to_match=rule.field_to_match.value, match_type=rule.match_type.value,
        pattern=rule.pattern, suggested_category_id=rule.suggested_category_id,
        priority=rule.priority, is_active=rule.is_active,
    )


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить правило",
)
async def delete_rule(
    company_id: UUID,
    rule_id:    UUID,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    rule = await _require_rule(db, rule_id, company_id)
    await db.delete(rule)
    await db.flush()
