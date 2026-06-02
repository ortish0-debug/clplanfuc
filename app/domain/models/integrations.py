"""
Доменный слой: банковские интеграции DirectBank (Спринт 8).

BankConnection — карточка подключённого банковского API к расчётному счёту
компании. Одна запись = один расчётный счёт в одном банке.

Поддерживаемые банки (bank_name):
  'tinkoff'   — Tинькофф Бизнес API (OAuth2, OpenBanking)
  'tochka'    — Точка Банк API (OpenID Connect)
  'sber'      — СберБизнес API (OAuth2)
  'alfa'      — Альфа-Банк API (OpenBanking)
  'custom'    — любой Open API-совместимый банк (via webhook)

Жизненный цикл токена:
  access_token  — короткоживущий JWT (обычно 30 мин — 24 ч)
  refresh_token — долгоживущий (обычно 90 дней), NULL для webhook-схем
  token_expires_at — UTC-время истечения access_token

В продакшене access_token и refresh_token ОБЯЗАТЕЛЬНО шифруются
симметричным ключом (AES-256-GCM) перед записью в БД.
Сервис шифрования подключается в роутере, модель хранит
уже зашифрованную строку.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.domain.models.finance import TimestampMixin

if TYPE_CHECKING:
    from app.domain.models.finance import Account, Company


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------


class BankConnectionStatus(str, enum.Enum):
    ACTIVE       = "active"        # Подключение работает, синхронизация штатная
    ERROR        = "error"         # Последняя попытка синхронизации завершилась ошибкой
    EXPIRED      = "expired"       # access_token истёк, нужна повторная авторизация
    DISCONNECTED = "disconnected"  # Отключено вручную пользователем


# ---------------------------------------------------------------------------
# BankConnection
# ---------------------------------------------------------------------------


class BankConnection(Base, TimestampMixin):
    """
    Подключение расчётного счёта компании к банковскому API.

    Одна запись соответствует одной паре (счёт × банк).
    Уникальность гарантируется ограничением uq_bank_connection_account.

    Схема синхронизации:
      1. Пользователь проходит OAuth2 в банке → получаем access/refresh токены
      2. Сохраняем зашифрованные токены в этой записи
      3. Планировщик периодически вызывает bank_sync_service.sync(connection)
      4. Сервис дёргает /transactions банковского API, создаёт Transaction-объекты
      5. Обновляет last_sync_at, при ошибке меняет status → ERROR
    """

    __tablename__ = "bank_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Привязанный расчётный счёт в нашей системе
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Идентификатор банка-партнёра
    bank_name: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Slug банка: tinkoff | tochka | sber | alfa | custom",
    )

    # ── Прямой API-доступ (Sprint 12) ────────────────────────────────────────
    # api_token — статический токен (API Key) для банков без OAuth2-потока.
    # Для банков с OAuth2 токены хранятся в access_token / refresh_token.
    api_token: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True,
        doc="Статический API-ключ (Base64/Bearer) для банков без OAuth2. AES-256 зашифрован.",
    )
    # Номер расчётного счёта (BBAN или IBAN) для верификации и диспетчеризации
    account_number: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
        doc="Банковский номер счёта (20-значный BBAN или IBAN) — для сверки при импорте",
    )

    # OAuth2 / OpenID токены (шифруются перед сохранением)
    access_token: Mapped[str] = mapped_column(
        Text, nullable=False,
        doc="Зашифрованный AES-256 access token банковского API",
    )
    refresh_token: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Зашифрованный refresh token (NULL для webhook-схем без refresh)",
    )
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="UTC-момент истечения access_token (NULL если токен бессрочный)",
    )

    # Состояние подключения
    status: Mapped[BankConnectionStatus] = mapped_column(
        Enum(
            BankConnectionStatus,
            name="bank_connection_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BankConnectionStatus.ACTIVE,
        nullable=False,
    )
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        doc="Время последней успешной синхронизации",
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Текст последней ошибки синхронизации (для диагностики)",
    )

    # Параметры синхронизации
    sync_from_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True,
        doc="С какой даты тянуть транзакции при первой синхронизации",
    )
    # Флаг: автоматически применять AI-классификатор к импортированным транзакциям
    auto_classify: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # Дополнительные данные конкретного банка (webhook_id, consent_id и т.д.)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Связи
    company: Mapped["Company"]  = relationship()
    account: Mapped["Account"]  = relationship(back_populates="bank_connection")
    sync_logs: Mapped[list["BankSyncLog"]] = relationship(
        back_populates="bank_connection",
        cascade="all, delete-orphan",
        order_by="BankSyncLog.created_at.desc()",
    )

    __table_args__ = (
        # Один счёт — одно подключение (запрещает дублирование)
        UniqueConstraint("account_id", name="uq_bank_connection_account"),
        Index("ix_bank_connections_company_id", "company_id"),
        Index("ix_bank_connections_status",     "company_id", "status"),
        Index("ix_bank_connections_bank_name",  "company_id", "bank_name"),
    )

    @property
    def is_token_expired(self) -> bool:
        """True если access_token уже истёк по времени."""
        if self.token_expires_at is None:
            return False
        from datetime import timezone
        return datetime.now(tz=timezone.utc) >= self.token_expires_at

    @property
    def needs_reauth(self) -> bool:
        """True если подключение требует повторной авторизации пользователя."""
        return self.status == BankConnectionStatus.EXPIRED or self.is_token_expired

    def mark_synced(self) -> None:
        """Вызывается сервисом после успешной синхронизации."""
        from datetime import timezone
        self.last_sync_at = datetime.now(tz=timezone.utc)
        self.last_error   = None
        self.status       = BankConnectionStatus.ACTIVE

    def mark_error(self, error_message: str) -> None:
        """Вызывается сервисом при ошибке синхронизации."""
        self.last_error = error_message[:4096]   # обрезаем длинные stacktrace
        self.status     = BankConnectionStatus.ERROR

    def __repr__(self) -> str:
        return (
            f"<BankConnection id={self.id} bank={self.bank_name!r} "
            f"account_id={self.account_id} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# BankSyncLog — журнал попыток синхронизации (Sprint 12)
# ---------------------------------------------------------------------------

_SYNC_LOG_STATUSES = ("processing", "success", "failed")


class BankSyncLog(Base):
    """
    Запись журнала одной попытки банковской синхронизации.

    Создаётся в начале каждого цикла bank_sync_service.sync():
      status='processing' → после обработки: 'success' или 'failed'.

    Хранит:
      - сколько транзакций получено из банка
      - текст ошибки при провале
      - время старта (created_at, через server_default=now())

    Используется для:
      - Отображения истории синхронизаций в UI
      - Алертов при повторяющихся ошибках (мониторинг)
      - Идемпотентности: не запускаем новый цикл, пока есть 'processing'

    status хранится как String(16) без PostgreSQL-enum:
      Enum-тип не нужен — значений мало, String проще мигрировать.
    """

    __tablename__ = "bank_sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    bank_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_connections.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 'processing' | 'success' | 'failed'
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="processing",
        doc="Статус попытки синхронизации",
    )

    # Количество транзакций, загруженных за этот цикл (0 при ошибке)
    transactions_fetched: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
        doc="Число транзакций, импортированных из банка за этот запуск",
    )

    # Текст ошибки при status='failed' (stacktrace, HTTP error и т.д.)
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        doc="Полный текст ошибки при status=failed",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        doc="Время начала попытки синхронизации",
    )

    # Связи
    company: Mapped["Company"] = relationship()
    bank_connection: Mapped["BankConnection"] = relationship(back_populates="sync_logs")

    __table_args__ = (
        Index("ix_bank_sync_logs_connection_id", "bank_connection_id"),
        Index("ix_bank_sync_logs_company_id",    "company_id"),
        Index("ix_bank_sync_logs_status",        "bank_connection_id", "status"),
        Index("ix_bank_sync_logs_created_at",    "bank_connection_id", "created_at"),
    )

    @property
    def is_running(self) -> bool:
        return self.status == "processing"

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def __repr__(self) -> str:
        return (
            f"<BankSyncLog id={self.id} "
            f"conn={self.bank_connection_id} "
            f"status={self.status!r} "
            f"fetched={self.transactions_fetched}>"
        )
