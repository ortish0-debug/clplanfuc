"""
Сервис: Массовые операции над транзакциями (Спринт 8).

bulk_update_transactions() — одним SQL UPDATE перекатегоризирует
  и/или привязывает к проекту список транзакций.

bulk_delete_transactions() — одним SQL UPDATE помечает список
  транзакций как удалённые (is_deleted=True, soft delete).

Оба метода:
  • Фильтруют по company_id → IDOR-защита на уровне сервиса.
  • Пропускают уже удалённые транзакции (is_deleted=False в WHERE).
  • Возвращают BulkOperationResult с точным счётчиком обновлённых строк.
  • Используют один SQL-запрос — не N round-trips к БД.

synchronize_session=False:
  Мы намеренно не обновляем объекты в identity map сессии,
  т.к. после массового UPDATE данные в кеше устарели. Роутер обязан
  либо вернуть только счётчик, либо перезагрузить нужные объекты.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Transaction


# ─────────────────────────────────────────────────────────────────────────────
# РЕЗУЛЬТАТ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BulkOperationResult:
    """Итог массовой операции."""
    requested: int   # сколько ID было передано
    updated:   int   # сколько строк фактически изменено
    skipped:   int   # не найдено / не принадлежат компании / уже удалены

    @property
    def success(self) -> bool:
        return self.updated > 0


# ─────────────────────────────────────────────────────────────────────────────
# МАССОВОЕ ОБНОВЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


async def bulk_update_transactions(
    db:              AsyncSession,
    company_id:      UUID,
    transaction_ids: list[UUID],
    category_id:     Optional[UUID] = None,
    project_id:      Optional[UUID] = None,
) -> BulkOperationResult:
    """
    Одним SQL UPDATE обновляет category_id и/или project_id
    для всех указанных транзакций компании.

    Аргументы:
        db              — AsyncSession
        company_id      — UUID компании (IDOR-фильтр)
        transaction_ids — список UUID транзакций для обновления
        category_id     — новая категория (None = не менять)
        project_id      — новый проект (None = не менять)
                          Передайте явный UUID, чтобы установить,
                          или используйте bulk_clear_project_id, чтобы обнулить.

    Возвращает BulkOperationResult с количеством обновлённых строк.

    Raises:
        ValueError: если не передан ни category_id, ни project_id.
    """
    if not transaction_ids:
        return BulkOperationResult(requested=0, updated=0, skipped=0)

    # Собираем только те поля, которые реально меняются
    update_values: dict = {}
    if category_id is not None:
        update_values["category_id"] = category_id
    if project_id is not None:
        update_values["project_id"] = project_id

    if not update_values:
        raise ValueError(
            "Необходимо передать хотя бы один параметр для обновления: "
            "category_id или project_id."
        )

    result = await db.execute(
        update(Transaction)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.id.in_(transaction_ids),
                Transaction.is_deleted.is_(False),
            )
        )
        .values(**update_values)
        # synchronize_session=False: не обновляем identity map —
        # объекты в сессии после этого вызова устарели
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    updated  = result.rowcount
    return BulkOperationResult(
        requested=len(transaction_ids),
        updated=updated,
        skipped=len(transaction_ids) - updated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# МАССОВОЕ ОБНУЛЕНИЕ ПРОЕКТА
# ─────────────────────────────────────────────────────────────────────────────


async def bulk_clear_project_id(
    db:              AsyncSession,
    company_id:      UUID,
    transaction_ids: list[UUID],
) -> BulkOperationResult:
    """
    Снимает привязку к проекту (project_id = NULL) для указанных транзакций.

    Выделено отдельно от bulk_update_transactions, потому что
    передача project_id=None в update_values не добавит поле в SET-clause,
    а нам нужно явно написать SET project_id = NULL.
    """
    if not transaction_ids:
        return BulkOperationResult(requested=0, updated=0, skipped=0)

    result = await db.execute(
        update(Transaction)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.id.in_(transaction_ids),
                Transaction.is_deleted.is_(False),
            )
        )
        .values(project_id=None)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    updated = result.rowcount
    return BulkOperationResult(
        requested=len(transaction_ids),
        updated=updated,
        skipped=len(transaction_ids) - updated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# МАССОВОЕ МЯГКОЕ УДАЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


async def bulk_delete_transactions(
    db:              AsyncSession,
    company_id:      UUID,
    transaction_ids: list[UUID],
) -> BulkOperationResult:
    """
    Помечает указанные транзакции как удалённые (is_deleted=True).

    Уже удалённые транзакции пропускаются (не увеличивают `updated`).
    Транзакции других компаний молча игнорируются — IDOR-защита.

    Одним SQL UPDATE — нет N+1 запросов.
    """
    if not transaction_ids:
        return BulkOperationResult(requested=0, updated=0, skipped=0)

    now = datetime.now(tz=timezone.utc)

    result = await db.execute(
        update(Transaction)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.id.in_(transaction_ids),
                Transaction.is_deleted.is_(False),   # только не удалённые
            )
        )
        .values(is_deleted=True, deleted_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    updated = result.rowcount
    return BulkOperationResult(
        requested=len(transaction_ids),
        updated=updated,
        skipped=len(transaction_ids) - updated,
    )


# ─────────────────────────────────────────────────────────────────────────────
# МАССОВОЕ ВОССТАНОВЛЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────


async def bulk_restore_transactions(
    db:              AsyncSession,
    company_id:      UUID,
    transaction_ids: list[UUID],
) -> BulkOperationResult:
    """
    Восстанавливает ранее удалённые транзакции (is_deleted → False).
    Используется при ошибочном удалении.
    """
    if not transaction_ids:
        return BulkOperationResult(requested=0, updated=0, skipped=0)

    result = await db.execute(
        update(Transaction)
        .where(
            and_(
                Transaction.company_id == company_id,
                Transaction.id.in_(transaction_ids),
                Transaction.is_deleted.is_(True),    # только удалённые
            )
        )
        .values(is_deleted=False, deleted_at=None)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    updated = result.rowcount
    return BulkOperationResult(
        requested=len(transaction_ids),
        updated=updated,
        skipped=len(transaction_ids) - updated,
    )
