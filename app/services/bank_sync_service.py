"""
Сервис: Банковская синхронизация с дедупликацией и AI-категоризацией (Спринт 12).

Полный цикл execute_bank_synchronization():
  1. IDOR-проверка: BankConnection принадлежит company_id
  2. Создать BankSyncLog(status='processing') → flush
  3. Имитировать запрос к банковскому API → список проводок
  4. Для каждой проводки:
     a. SELECT WHERE bank_transaction_id = :id  → дубль? → пропустить
     b. INSERT Transaction
     c. Прогнать через движок AutoRule → подставить category_id
  5. Обновить connection.last_sync_at
  6. BankSyncLog.status = 'success', transactions_fetched = N
  При исключении → BankSyncLog.status = 'failed', error_message = str(e)

Движок AutoRule (_apply_auto_rules):
  - Загружает активные правила компании (ORDER BY priority DESC)
  - MatchField:  DESCRIPTION | COUNTERPARTY_NAME | COUNTERPARTY_INN
  - MatchType:   CONTAINS (без регистра) | EXACT (без регистра) | REGEX
  - Первое совпавшее правило устанавливает category_id + ai_classified=True
  - Если ни одно не совпало — категория не выставляется

Идемпотентность:
  Каждая «банковская» транзакция имеет детерминированный bank_transaction_id
  вида sha256("sync:{connection_id}:{date}:{index}")[:32].
  Повторная синхронизация в тот же день создаёт 0 транзакций (все дубли).
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType
from app.domain.models.integrations import BankConnection, BankConnectionStatus, BankSyncLog
from app.domain.models.rules import AutoRule, MatchField, MatchType

# ─────────────────────────────────────────────────────────────────────────────
# ШАБЛОНЫ МОКОВОГО БАНКОВСКОГО API
# ─────────────────────────────────────────────────────────────────────────────

_MOCK_TEMPLATES = [
    (TransactionType.INCOME,  Decimal("120000.00"), "Поступление выручки от клиента"),
    (TransactionType.EXPENSE, Decimal("35000.00"),  "Оплата поставщику"),
    (TransactionType.INCOME,  Decimal("89000.00"),  "Перевод от контрагента"),
    (TransactionType.EXPENSE, Decimal("14500.00"),  "Комиссия банка"),
    (TransactionType.EXPENSE, Decimal("62000.00"),  "Оплата аренды офиса"),
]


@dataclass
class MockBankTransaction:
    """Одна банковская проводка из мокового API."""
    bank_transaction_id: str
    txn_type:            TransactionType
    amount:              Decimal
    description:         str
    txn_date:            date
    currency:            str = "RUB"


# ─────────────────────────────────────────────────────────────────────────────
# ГЕНЕРАЦИЯ МОКОВОГО ОТВЕТА БАНКОВСКОГО API
# ─────────────────────────────────────────────────────────────────────────────


def _fake_bank_tx_id(connection_id: UUID, txn_date: date, index: int) -> str:
    """
    Детерминированный bank_transaction_id: одинаковый при повторном вызове
    за ту же дату → PostgreSQL ON CONFLICT отклонит дубль.
    """
    raw = f"sync:{connection_id}:{txn_date.isoformat()}:{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _generate_mock_bank_transactions(
    connection_id: UUID,
    count: int = 5,
) -> list[MockBankTransaction]:
    """Имитирует ответ банковского API: возвращает count проводок за текущий день."""
    today      = date.today()
    templates  = _MOCK_TEMPLATES[:count]
    result     = []

    for idx, (txn_type, amount, description) in enumerate(templates):
        result.append(MockBankTransaction(
            bank_transaction_id=_fake_bank_tx_id(connection_id, today, idx),
            txn_type=txn_type,
            amount=amount,
            description=description,
            txn_date=today,
        ))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# ДВИЖОК ПРАВИЛ АВТОКАТЕГОРИЗАЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _load_auto_rules(
    db:         AsyncSession,
    company_id: UUID,
) -> list[AutoRule]:
    """Загружает активные AutoRule компании, отсортированные по убыванию priority."""
    result = await db.execute(
        select(AutoRule)
        .where(
            and_(
                AutoRule.company_id == company_id,
                AutoRule.is_active.is_(True),
            )
        )
        .order_by(AutoRule.priority.desc())
    )
    return list(result.scalars().all())


def _rule_matches(rule: AutoRule, txn: Transaction) -> bool:
    """
    Проверяет, соответствует ли транзакция правилу.

    MatchField:
      DESCRIPTION      → txn.description
      COUNTERPARTY_NAME → txn.counterparty
      COUNTERPARTY_INN  → txn.meta.get('inn') или txn.counterparty

    MatchType:
      CONTAINS → case-insensitive подстрока
      EXACT    → case-insensitive точное совпадение
      REGEX    → re.search с IGNORECASE
    """
    pattern = rule.pattern or ""
    if not pattern:
        return False

    # Выбираем целевое поле
    if rule.field_to_match == MatchField.DESCRIPTION:
        target = txn.description or ""
    elif rule.field_to_match == MatchField.COUNTERPARTY_NAME:
        target = txn.counterparty or ""
    elif rule.field_to_match == MatchField.COUNTERPARTY_INN:
        # INN может быть в meta или в counterparty-строке
        target = str(txn.meta.get("inn", "")) or txn.counterparty or ""
    else:
        return False

    # Применяем тип совпадения
    try:
        if rule.match_type == MatchType.CONTAINS:
            return pattern.lower() in target.lower()
        elif rule.match_type == MatchType.EXACT:
            return pattern.lower() == target.lower()
        elif rule.match_type == MatchType.REGEX:
            return bool(re.search(pattern, target, re.IGNORECASE))
    except re.error:
        # Некорректный regex — пропускаем правило, не падаем
        return False

    return False


async def _apply_auto_rules(
    db:         AsyncSession,
    company_id: UUID,
    txn:        Transaction,
    rules:      list[AutoRule],
) -> bool:
    """
    Прогоняет транзакцию через активные правила компании.

    Возвращает True если правило сработало (category_id выставлен).
    Мутирует объект txn в памяти; вызывающий должен вызвать flush.
    """
    for rule in rules:
        if _rule_matches(rule, txn) and rule.suggested_category_id:
            txn.category_id  = rule.suggested_category_id
            txn.ai_classified = True
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# ОСНОВНАЯ ФУНКЦИЯ СИНХРОНИЗАЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def execute_bank_synchronization(
    db:            AsyncSession,
    company_id:    UUID,
    connection_id: UUID,
) -> BankSyncLog:
    """
    Полный цикл банковской синхронизации.

    Шаги:
      1. IDOR: загружаем BankConnection, проверяем company_id
      2. Создаём BankSyncLog(status='processing'), flush
      3. Генерируем мок-транзакции из «банковского API»
      4. Для каждой — дедупликация по bank_transaction_id
      5. Новые — INSERT + AutoRule-категоризация
      6. Обновляем connection.last_sync_at, статус лога → 'success'
      Catch: статус лога → 'failed', error_message

    Returns:
        Итоговый BankSyncLog (status='success' или 'failed').

    Raises:
        HTTPException 404 — подключение не найдено
        HTTPException 422 — подключение отключено
    """
    # ── 1. IDOR + проверка состояния ──────────────────────────────────────────
    conn_result = await db.execute(
        select(BankConnection).where(
            and_(
                BankConnection.id         == connection_id,
                BankConnection.company_id == company_id,
            )
        )
    )
    conn: Optional[BankConnection] = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Банковское подключение {connection_id} не найдено.",
        )
    if conn.status == BankConnectionStatus.DISCONNECTED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Подключение отключено. Переподключите банк перед синхронизацией.",
        )

    # ── 2. Создаём лог синхронизации ──────────────────────────────────────────
    sync_log = BankSyncLog(
        id=uuid.uuid4(),
        company_id=company_id,
        bank_connection_id=connection_id,
        status="processing",
        transactions_fetched=0,
    )
    db.add(sync_log)
    await db.flush()  # получаем sync_log.id без коммита

    try:
        # ── 3. «Запрос к банковскому API» ─────────────────────────────────────
        mock_txns = _generate_mock_bank_transactions(connection_id, count=5)

        # ── 4. Загружаем счёт для определения валюты ──────────────────────────
        account_result = await db.execute(
            select(Account).where(Account.id == conn.account_id)
        )
        account: Optional[Account] = account_result.scalar_one_or_none()
        if not account:
            raise RuntimeError(
                f"Привязанный счёт {conn.account_id} не найден. Обновите подключение."
            )
        currency = account.currency.value if hasattr(account.currency, "value") else str(account.currency)

        # ── 5. Загружаем правила автокатегоризации ────────────────────────────
        auto_rules = await _load_auto_rules(db, company_id)

        # ── 6. Импорт с дедупликацией ─────────────────────────────────────────
        imported_count = 0

        for mock_txn in mock_txns:
            # Проверяем дубль по bank_transaction_id
            dup_check = await db.execute(
                select(Transaction.id).where(
                    and_(
                        Transaction.account_id == conn.account_id,
                        Transaction.bank_transaction_id == mock_txn.bank_transaction_id,
                    )
                ).limit(1)
            )
            if dup_check.scalar_one_or_none() is not None:
                continue   # Дубль — тихо пропускаем

            # Создаём новую транзакцию
            txn = Transaction(
                id=uuid.uuid4(),
                company_id=company_id,
                account_id=conn.account_id,
                transaction_type=mock_txn.txn_type,
                status=TransactionStatus.CONFIRMED,
                amount=mock_txn.amount,
                currency=currency,
                exchange_rate=Decimal("1.000000"),
                amount_base_currency=mock_txn.amount,
                payment_date=mock_txn.txn_date,
                accrual_date=mock_txn.txn_date,
                description=f"[{conn.bank_name.upper()}] {mock_txn.description}",
                bank_transaction_id=mock_txn.bank_transaction_id,
                tags=[],
                meta={"source": "bank_sync", "bank": conn.bank_name},
                ai_classified=False,
                is_deleted=False,
                is_intra_group=False,
            )
            db.add(txn)
            await db.flush()   # нужен txn.id для возможных ссылок

            # AI-категоризация через AutoRule-движок
            await _apply_auto_rules(db, company_id, txn, auto_rules)

            imported_count += 1

        # ── 7. Обновляем подключение и лог ────────────────────────────────────
        conn.last_sync_at = datetime.now(tz=timezone.utc)
        conn.last_error   = None
        conn.status       = BankConnectionStatus.ACTIVE

        sync_log.status              = "success"
        sync_log.transactions_fetched = imported_count

        await db.flush()

    except Exception as exc:
        # Атомарно помечаем лог как провальный
        sync_log.status        = "failed"
        sync_log.error_message = str(exc)[:4096]
        conn.mark_error(str(exc)[:512])
        await db.flush()
        # Не перебрасываем: возвращаем лог с ошибкой — роутер решит что делать
    return sync_log


# ─────────────────────────────────────────────────────────────────────────────
# ИСТОРИЯ СИНХРОНИЗАЦИЙ
# ─────────────────────────────────────────────────────────────────────────────


async def get_connection_sync_history(
    db:            AsyncSession,
    company_id:    UUID,
    connection_id: UUID,
    limit:         int = 10,
) -> list[BankSyncLog]:
    """
    Последние N сессий синхронизации для конкретного подключения.

    IDOR: фильтрует по company_id → пользователь не увидит чужие логи.

    Используется для:
      - Отображения статуса последней синхронизации в UI
      - Диагностики повторяющихся ошибок
      - Отображения истории импортов в разделе DirectBank
    """
    result = await db.execute(
        select(BankSyncLog)
        .where(
            and_(
                BankSyncLog.bank_connection_id == connection_id,
                BankSyncLog.company_id         == company_id,
            )
        )
        .order_by(BankSyncLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
