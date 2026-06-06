"""
Сервис импорта банковских выписок формата 1С Клиент-Банк.

Формат файла (RFC-подобный, CP1251 или UTF-8):
──────────────────────────────────────────────
1CClientBankExchange
ВерсияФормата=1.02
Кодировка=Windows
РасчетныйСчет=40702810XXXXXXXXXX
ДатаНачала=01.01.2025
ДатаКонца=31.01.2025

СекцияРасчСчет
  НачальныйОстаток=100000.00
  КонечныйОстаток=250000.00
  РасчетныйСчет=40702810XXXXXXXXXX
КонецРасчСчет

СекцияДокумент=Платежное поручение
  Номер=1234
  Дата=15.01.2025
  Сумма=15000.00
  ПлательщикСчет=40702810XXXXXXXXXX  ← если наш счёт = РАСХОД
  ПлательщикИНН=7700000001
  Плательщик1=ООО МояКомпания
  ПолучательСчет=40702810YYYYYYYYYY
  ПолучательИНН=7700000002
  Получатель1=ООО Поставщик
  НазначениеПлатежа=Оплата по договору №123
КонецДокумента
──────────────────────────────────────────────
Направление: ПлательщикСчет == наш счёт → EXPENSE, иначе → INCOME.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.rules import AutoRule, MatchField, MatchType

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ТИПЫ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ParsedBankTransaction:
    """Одна транзакция, извлечённая из файла выписки."""
    doc_number:          Optional[str]
    payment_date:        date
    amount:              Decimal
    transaction_type:    str              # "income" | "expense"
    counterparty_name:   Optional[str]
    counterparty_inn:    Optional[str]
    counterparty_account:Optional[str]
    description:         str
    our_account:         Optional[str]
    # Заполняется движком правил
    suggested_category_id:   Optional[UUID] = None
    suggested_category_name: Optional[str]  = None
    rule_name:               Optional[str]  = None
    raw_fields:              dict           = field(default_factory=dict)


@dataclass
class BankStatementData:
    """Полная структура разобранной выписки."""
    our_account:     Optional[str]
    bank_name:       Optional[str]
    period_from:     Optional[date]
    period_to:       Optional[date]
    opening_balance: Optional[Decimal]
    closing_balance: Optional[Decimal]
    transactions:    list[ParsedBankTransaction] = field(default_factory=list)

    @property
    def income_count(self) -> int:
        return sum(1 for t in self.transactions if t.transaction_type == "income")

    @property
    def expense_count(self) -> int:
        return sum(1 for t in self.transactions if t.transaction_type == "expense")

    @property
    def total_income(self) -> Decimal:
        return sum(
            (t.amount for t in self.transactions if t.transaction_type == "income"),
            Decimal("0.00"),
        )

    @property
    def total_expense(self) -> Decimal:
        return sum(
            (t.amount for t in self.transactions if t.transaction_type == "expense"),
            Decimal("0.00"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# ДЕКОДИРОВАНИЕ ФАЙЛА
# ─────────────────────────────────────────────────────────────────────────────


def _detect_and_decode(raw: bytes) -> str:
    """
    Определяет кодировку файла и декодирует байты в строку.
    Приоритет: UTF-8 → CP1251 → Latin-1 (fallback без ошибок).
    """
    # Сначала пробуем UTF-8 (с BOM и без)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # Проверяем метаданные файла на наличие "Кодировка=Windows"
    sniff = raw[:512]
    try:
        sniff_str = sniff.decode("cp1251", errors="replace")
    except Exception:
        sniff_str = ""

    if "Windows" in sniff_str or "windows" in sniff_str:
        return raw.decode("cp1251", errors="replace")

    # UTF-8 с заменой невалидных символов
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# ПАРСИНГ СТРОК В СЛОВАРЬ ПОЛЕЙ
# ─────────────────────────────────────────────────────────────────────────────


def _parse_section_fields(lines: list[str]) -> dict[str, str]:
    """
    Разбирает набор строк вида KEY=VALUE в словарь.
    Многострочные поля (НазначениеПлатежа, НазначениеПлатежа1, …)
    конкатенируются в одно значение.
    """
    result: dict[str, str] = {}
    purpose_parts: list[str] = []

    for line in lines:
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key   = key.strip()
        value = value.strip()

        # Собираем части назначения платежа
        if re.match(r"НазначениеПлатежа\d*$", key):
            purpose_parts.append(value)
            continue

        result[key] = value

    if purpose_parts:
        result["НазначениеПлатежа"] = " ".join(purpose_parts)

    return result


def _parse_decimal(raw: str) -> Optional[Decimal]:
    """Безопасно преобразует строку суммы в Decimal."""
    if not raw:
        return None
    # Заменяем запятую на точку, убираем пробелы-разделители тысяч
    cleaned = raw.replace(",", ".").replace(" ", "").replace("\xa0", "")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _parse_date(raw: str) -> Optional[date]:
    """Разбирает дату в формате DD.MM.YYYY или YYYY-MM-DD."""
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    logger.warning("bank_importer: не удалось разобрать дату: %r", raw)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# РАЗБОР ГЛОБАЛЬНОГО ЗАГОЛОВКА
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _HeaderData:
    our_account:     Optional[str]    = None
    bank_name:       Optional[str]    = None
    period_from:     Optional[date]   = None
    period_to:       Optional[date]   = None
    opening_balance: Optional[Decimal]= None
    closing_balance: Optional[Decimal]= None


def _parse_global_header(header_lines: list[str]) -> _HeaderData:
    fields  = _parse_section_fields(header_lines)
    hd      = _HeaderData()
    hd.our_account     = fields.get("РасчетныйСчет")
    hd.bank_name       = fields.get("Банк") or fields.get("Отправитель")
    hd.period_from     = _parse_date(fields.get("ДатаНачала", ""))
    hd.period_to       = _parse_date(fields.get("ДатаКонца",  ""))
    return hd


def _parse_account_section(section_lines: list[str], hd: _HeaderData) -> None:
    """Обновляет hd данными из СекцияРасчСчет."""
    fields = _parse_section_fields(section_lines)
    if not hd.our_account:
        hd.our_account = fields.get("РасчетныйСчет")
    hd.opening_balance = _parse_decimal(fields.get("НачальныйОстаток", ""))
    hd.closing_balance = _parse_decimal(fields.get("КонечныйОстаток", ""))
    if not hd.period_from:
        hd.period_from = _parse_date(fields.get("ДатаНачала", ""))
    if not hd.period_to:
        hd.period_to = _parse_date(fields.get("ДатаКонца", ""))


# ─────────────────────────────────────────────────────────────────────────────
# РАЗБОР ОДНОЙ ТРАНЗАКЦИИ
# ─────────────────────────────────────────────────────────────────────────────


def _parse_doc_section(
    section_lines: list[str],
    our_account: Optional[str],
) -> Optional[ParsedBankTransaction]:
    """
    Разбирает СекцияДокумент в ParsedBankTransaction.
    Возвращает None если не удалось извлечь обязательные поля (дата, сумма).
    """
    fields = _parse_section_fields(section_lines)

    amount = _parse_decimal(fields.get("Сумма", ""))
    if amount is None or amount <= Decimal("0"):
        return None

    pay_date = _parse_date(fields.get("Дата", ""))
    if pay_date is None:
        return None

    payer_account    = (fields.get("ПлательщикСчет", "") or "").strip()
    recipient_account= (fields.get("ПолучательСчет", "") or "").strip()

    # Определяем направление по совпадению с нашим счётом
    txn_type = "expense"  # по умолчанию расход
    if our_account:
        our_clean = re.sub(r"\s+", "", our_account)
        if re.sub(r"\s+", "", recipient_account) == our_clean:
            txn_type = "income"
        elif re.sub(r"\s+", "", payer_account) == our_clean:
            txn_type = "expense"
    else:
        # Нет информации о нашем счёте: смотрим на тип документа
        doc_type = fields.get("_section_type", "").lower()
        if any(kw in doc_type for kw in ("поступ", "приход", "входящ", "зачисл")):
            txn_type = "income"

    # Контрагент — тот, кто «с другой стороны»
    if txn_type == "expense":
        cparty_name    = fields.get("Получатель1") or fields.get("Получатель")
        cparty_inn     = fields.get("ПолучательИНН")
        cparty_account = recipient_account or None
    else:
        cparty_name    = fields.get("Плательщик1") or fields.get("Плательщик")
        cparty_inn     = fields.get("ПлательщикИНН")
        cparty_account = payer_account or None

    description = fields.get("НазначениеПлатежа", "").strip() or "Без назначения"

    return ParsedBankTransaction(
        doc_number           = fields.get("Номер"),
        payment_date         = pay_date,
        amount               = amount,
        transaction_type     = txn_type,
        counterparty_name    = cparty_name,
        counterparty_inn     = cparty_inn,
        counterparty_account = cparty_account,
        description          = description[:500],
        our_account          = our_account,
        raw_fields           = fields,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ДВИЖОК ПРАВИЛ АВТОКАТЕГОРИЗАЦИИ
# ─────────────────────────────────────────────────────────────────────────────


async def _load_rules(
    db: AsyncSession,
    company_id: UUID,
) -> list[AutoRule]:
    """Загружает активные правила компании, отсортированные по убыванию приоритета."""
    result = await db.execute(
        select(AutoRule)
        .where(
            and_(
                AutoRule.company_id == company_id,
                AutoRule.is_active.is_(True),
            )
        )
        .order_by(AutoRule.priority.desc(), AutoRule.created_at)
    )
    return list(result.scalars().all())


def _match_rule(rule: AutoRule, txn: ParsedBankTransaction) -> bool:
    """Проверяет, совпадает ли правило с транзакцией."""
    target_map = {
        MatchField.DESCRIPTION:       txn.description       or "",
        MatchField.COUNTERPARTY_INN:  txn.counterparty_inn  or "",
        MatchField.COUNTERPARTY_NAME: txn.counterparty_name or "",
    }
    target = target_map.get(rule.field_to_match, "")
    pattern = rule.pattern

    try:
        if rule.match_type == MatchType.CONTAINS:
            return pattern.lower() in target.lower()
        if rule.match_type == MatchType.EXACT:
            return pattern.lower() == target.lower()
        if rule.match_type == MatchType.REGEX:
            return bool(re.search(pattern, target, re.IGNORECASE | re.UNICODE))
    except re.error as exc:
        logger.warning("Невалидный regex в правиле %s: %s", rule.id, exc)
    return False


def _apply_rules(
    transactions: list[ParsedBankTransaction],
    rules: list[AutoRule],
    category_names: dict[UUID, str],
) -> None:
    """
    Мутирует список транзакций: подставляет category_id и rule_name
    по первому совпавшему правилу (in-place).
    """
    for txn in transactions:
        for rule in rules:
            if _match_rule(rule, txn):
                txn.suggested_category_id   = rule.suggested_category_id
                txn.suggested_category_name = (
                    category_names.get(rule.suggested_category_id, "")
                    if rule.suggested_category_id else None
                )
                txn.rule_name = rule.name
                break  # первое совпадение — стоп


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ ПАРСИНГА
# ─────────────────────────────────────────────────────────────────────────────


async def parse_1c_statement(
    raw_bytes: bytes,
    company_id: UUID,
    db: AsyncSession,
) -> BankStatementData:
    """
    Разбирает сырые байты файла выписки 1С Клиент-Банк.

    1. Определяет кодировку и декодирует файл.
    2. Разбивает файл на секции (заголовок / РасчСчет / Документ).
    3. Извлекает транзакции, определяет направление (доход/расход).
    4. Загружает AutoRule-правила компании и проставляет категории.
    5. Возвращает BankStatementData.
    """
    content = _detect_and_decode(raw_bytes)
    lines   = content.splitlines()

    # ── Первый проход: разбиваем на секции ──────────────────────────────────
    header_lines:   list[str]        = []
    transactions:   list[ParsedBankTransaction] = []
    hd              = _HeaderData()
    in_account_sec  = False
    in_doc_sec      = False
    current_doc:    list[str]        = []
    current_type    = ""

    for line in lines:
        stripped = line.strip()

        # --- Конец секции счёта ---
        if stripped == "КонецРасчСчет":
            _parse_account_section(current_doc, hd)
            current_doc  = []
            in_account_sec = False
            continue

        # --- Конец документа ---
        if stripped == "КонецДокумента":
            if in_doc_sec:
                # Передаём тип документа для определения направления
                current_doc.append(f"_section_type={current_type}")
                txn = _parse_doc_section(current_doc, hd.our_account)
                if txn:
                    transactions.append(txn)
            current_doc = []
            in_doc_sec  = False
            current_type= ""
            continue

        # --- Начало секции счёта ---
        if stripped == "СекцияРасчСчет" or stripped.startswith("СекцияРасчСчет="):
            in_account_sec = True
            current_doc    = []
            continue

        # --- Начало документа ---
        if stripped.startswith("СекцияДокумент="):
            in_doc_sec   = True
            current_doc  = []
            current_type = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            continue

        # --- Сбор строк текущей секции ---
        if in_account_sec or in_doc_sec:
            current_doc.append(line)
        else:
            header_lines.append(line)

    # Разбираем глобальный заголовок
    hd_from_header = _parse_global_header(header_lines)
    if not hd.our_account:
        hd.our_account = hd_from_header.our_account
    if not hd.bank_name:
        hd.bank_name   = hd_from_header.bank_name
    if not hd.period_from:
        hd.period_from = hd_from_header.period_from
    if not hd.period_to:
        hd.period_to   = hd_from_header.period_to

    # ── Обогащаем our_account у транзакций (мог стать известен позже) ───────
    for txn in transactions:
        if txn.our_account is None:
            txn.our_account = hd.our_account

    # ── Движок правил: загружаем и применяем ────────────────────────────────
    rules = await _load_rules(db, company_id)
    if rules:
        # Для имён категорий строим словарь UUID→name
        cat_ids = {r.suggested_category_id for r in rules if r.suggested_category_id}
        category_names: dict[UUID, str] = {}
        if cat_ids:
            from sqlalchemy import select as sa_select
            from app.domain.models.finance import Category
            cat_result = await db.execute(
                sa_select(Category.id, Category.name).where(Category.id.in_(cat_ids))
            )
            category_names = {row.id: row.name for row in cat_result}

        _apply_rules(transactions, rules, category_names)
        logger.info(
            "bank_importer: применено %d правил к %d транзакциям (company=%s)",
            len(rules), len(transactions), company_id,
        )
    else:
        logger.debug("bank_importer: нет активных правил для company=%s", company_id)

    logger.info(
        "bank_importer: разобрано %d транзакций (account=%s, period=%s..%s)",
        len(transactions), hd.our_account, hd.period_from, hd.period_to,
    )

    return BankStatementData(
        our_account     = hd.our_account,
        bank_name       = hd.bank_name,
        period_from     = hd.period_from,
        period_to       = hd.period_to,
        opening_balance = hd.opening_balance,
        closing_balance = hd.closing_balance,
        transactions    = transactions,
    )
