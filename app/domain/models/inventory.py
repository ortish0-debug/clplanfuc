"""
Доменный слой: Складской учёт и партионный расчёт COGS (Спринт 13).

Поддерживается метод FIFO (First In — First Out):
  - Каждый приход (incoming) записывается с batch_id (UUID партии).
  - При списании (outgoing) сервис выбирает партии в порядке
    их поступления (operation_date ASC) и «закрывает» их.
  - Себестоимость проданных товаров (COGS) = SUM(outgoing.unit_price × qty).

Три типа операций:
  incoming  — приход товара на склад (закупка, возврат от клиента)
  outgoing  — расход со склада (продажа, списание)
  transfer  — перемещение между складами (warehouse_id → destination_warehouse_id)

Архитектура:
  StockItem     — номенклатурная карточка товара (company-scoped)
  Warehouse     — склад компании
  StockOperation — каждое движение товара (double-entry через batch_id)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import TimestampMixin, SoftDeleteMixin

if TYPE_CHECKING:
    from app.domain.models.finance import Company


# ─────────────────────────────────────────────────────────────────────────────
# ПЕРЕЧИСЛЕНИЯ
# ─────────────────────────────────────────────────────────────────────────────


class StockOperationType(str, enum.Enum):
    """
    Тип складской операции.

    INCOMING  — приход: закупка, поступление от поставщика, возврат от клиента.
    OUTGOING  — расход: продажа, списание брака, расход на производство.
    TRANSFER  — перемещение: со склада warehouse_id на destination_warehouse_id.
    """
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    TRANSFER = "transfer"


# ─────────────────────────────────────────────────────────────────────────────
# STOCK ITEM — Номенклатурная карточка
# ─────────────────────────────────────────────────────────────────────────────


class StockItem(Base, TimestampMixin, SoftDeleteMixin):
    """
    Товар / позиция номенклатуры в справочнике компании.

    Один StockItem — один вид товара или услуги.
    StockOperation ссылается на StockItem через stock_item_id.

    Расширение (вне Спринта 13): VAT-ставка на товар, штрих-код,
    минимальный остаток для авто-заказа, привязка к 1С-коду.
    """

    __tablename__ = "stock_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(512), nullable=False,
        doc="Наименование товара / позиции номенклатуры",
    )
    # Артикул / складской код
    sku: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True,
        doc="SKU — складской артикул (штрих-код, 1С-код)",
    )
    # Единица измерения: шт., кг, м, л, упак. и т.д.
    unit: Mapped[str] = mapped_column(
        String(32), nullable=False, default="шт.",
        doc="Единица измерения",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Описание товара / характеристики",
    )

    # Связи
    company:    Mapped["Company"]          = relationship(back_populates="stock_items")
    operations: Mapped[list["StockOperation"]] = relationship(
        back_populates="stock_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_stock_items_company_id", "company_id"),
        Index("ix_stock_items_company_sku", "company_id", "sku"),
    )

    def __repr__(self) -> str:
        return f"<StockItem id={self.id} sku={self.sku!r} name={self.name!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# WAREHOUSE — Склад
# ─────────────────────────────────────────────────────────────────────────────


class Warehouse(Base, TimestampMixin, SoftDeleteMixin):
    """
    Физический или виртуальный склад компании.

    Примеры: «Основной склад», «Магазин на Тверской», «Виртуальный (самовывоз)».
    StockOperation указывает warehouse_id (источник) и опционально
    destination_warehouse_id (получатель при типе TRANSFER).
    """

    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        doc="Название склада",
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True,
        doc="Адрес или описание расположения",
    )

    # Связи
    company:            Mapped["Company"]              = relationship(back_populates="warehouses")
    operations_as_src:  Mapped[list["StockOperation"]] = relationship(
        foreign_keys="StockOperation.warehouse_id",
        back_populates="warehouse",
        cascade="all, delete-orphan",
    )
    operations_as_dest: Mapped[list["StockOperation"]] = relationship(
        foreign_keys="StockOperation.destination_warehouse_id",
        back_populates="destination_warehouse",
    )

    __table_args__ = (
        Index("ix_warehouses_company_id", "company_id"),
    )

    def __repr__(self) -> str:
        return f"<Warehouse id={self.id} name={self.name!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# STOCK OPERATION — Движение товара
# ─────────────────────────────────────────────────────────────────────────────


class StockOperation(Base, TimestampMixin):
    """
    Одно движение товара (приход, расход, перемещение).

    FIFO через batch_id:
      При поступлении партии (incoming) сервис создаёт batch_id = uuid.uuid4().
      При списании (outgoing) сервис выбирает записи incoming WHERE stock_item_id = X
      ORDER BY operation_date ASC и «расходует» количество из каждой партии.
      Если партия полностью закрыта → batch_id списания указывает на первоначальный
      batch_id прихода для аудита.

    destination_warehouse_id:
      NULL для incoming/outgoing. Заполнен только при operation_type = TRANSFER.

    Строгость: операции НЕ мягко удаляются (нет is_deleted).
    Корректировка через обратную операцию (сторнирование).
    """

    __tablename__ = "stock_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Склад-источник (для TRANSFER = откуда)",
    )
    # Склад-получатель (только для TRANSFER)
    destination_warehouse_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Склад-получатель (только для operation_type=TRANSFER)",
    )
    stock_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stock_items.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Номенклатурная позиция",
    )

    # Тип операции — хранится как lowercase строка в PostgreSQL
    operation_type: Mapped[StockOperationType] = mapped_column(
        Enum(
            StockOperationType,
            name="stock_operation_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # ── Количество и стоимость ───────────────────────────────────────────────
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False,
        doc="Количество товара (3 знака после запятой для дробных единиц)",
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Цена за единицу на дату операции, ₽",
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False,
        doc="Итоговая сумма = quantity × unit_price (хранится явно для скорости)",
    )

    # ── Партионный учёт (FIFO) ──────────────────────────────────────────────
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        doc=(
            "Идентификатор партии для FIFO. "
            "incoming: batch_id = uuid новой партии. "
            "outgoing: batch_id = batch_id закрываемого прихода. "
            "transfer: NULL."
        ),
    )

    # ── Дата и время ────────────────────────────────────────────────────────
    operation_date: Mapped[date] = mapped_column(
        Date, nullable=False,
        doc="Дата складской операции (используется для FIFO-сортировки по дате прихода)",
    )

    # notes для аудита / описания операции
    notes: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Примечание к операции (накладная №, причина списания...)",
    )

    # Связи
    company:   Mapped["Company"]   = relationship(back_populates="stock_operations")
    warehouse: Mapped["Warehouse"] = relationship(
        foreign_keys=[warehouse_id],
        back_populates="operations_as_src",
    )
    destination_warehouse: Mapped[Optional["Warehouse"]] = relationship(
        foreign_keys=[destination_warehouse_id],
        back_populates="operations_as_dest",
    )
    stock_item: Mapped["StockItem"] = relationship(back_populates="operations")

    __table_args__ = (
        # Производительность: выборки остатков по складу+товару+дате
        Index("ix_stock_ops_company_id",       "company_id"),
        Index("ix_stock_ops_warehouse_id",      "warehouse_id"),
        Index("ix_stock_ops_stock_item_id",     "stock_item_id"),
        Index("ix_stock_ops_operation_date",    "company_id", "operation_date"),
        # FIFO: поиск и сортировка партий
        Index("ix_stock_ops_batch_id",          "batch_id"),
        # Комбинированный для FIFO-запросов: товар + склад + дата
        Index("ix_stock_ops_item_wh_date",      "stock_item_id", "warehouse_id", "operation_date"),
    )

    @property
    def is_incoming(self) -> bool:
        return self.operation_type == StockOperationType.INCOMING

    @property
    def is_outgoing(self) -> bool:
        return self.operation_type == StockOperationType.OUTGOING

    def __repr__(self) -> str:
        return (
            f"<StockOperation id={self.id} "
            f"type={self.operation_type} "
            f"qty={self.quantity} "
            f"item={self.stock_item_id}>"
        )
