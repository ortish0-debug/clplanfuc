"""Currency exchange rates service: CBR (Central Bank of Russia) integration."""
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.currency import CurrencyRate
from app.domain.models.finance import Account

logger = logging.getLogger(__name__)

# Target currencies for business operations (to RUB)
TARGET_CURRENCIES = ["USD", "EUR", "CNY", "BYN", "KZT"]

CBR_API_URL = "https://www.cbr-xml-daily.ru/daily_json.js"


async def fetch_and_store_cbr_rates(
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> None:
    """
    Fetch currency rates from CBR (Central Bank of Russia) and store in DB.

    Args:
        db: Database session
        target_date: Date to fetch rates for (default: today)

    Returns:
        None
    """
    if target_date is None:
        target_date = date.today()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(CBR_API_URL)
            response.raise_for_status()

        data = response.json()
        rates_dict = data.get("Valute", {})

        for currency in TARGET_CURRENCIES:
            if currency not in rates_dict:
                logger.warning(f"Currency {currency} not found in CBR response")
                continue

            rate_value = float(rates_dict[currency]["Value"])

            # Upsert: update if exists, insert if not
            existing = await db.execute(
                select(CurrencyRate).where(
                    and_(
                        CurrencyRate.currency_code == currency,
                        CurrencyRate.rate_date == target_date,
                    )
                )
            )
            rate_record = existing.scalar_one_or_none()

            if rate_record:
                rate_record.rate = rate_value
            else:
                rate_record = CurrencyRate(
                    currency_code=currency,
                    rate=rate_value,
                    rate_date=target_date,
                )
                db.add(rate_record)

        await db.flush()
        logger.info(
            f"Successfully stored currency rates for {target_date}: {TARGET_CURRENCIES}"
        )

    except httpx.RequestError as e:
        logger.error(f"Network error fetching CBR rates: {e}")
        # Fallback: use rate from previous day
        await _fallback_to_previous_day(db, target_date)
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Error parsing CBR response: {e}")
        await _fallback_to_previous_day(db, target_date)


async def _fallback_to_previous_day(
    db: AsyncSession,
    target_date: date,
) -> None:
    """
    Fallback: copy rates from previous day if API fetch fails.

    Args:
        db: Database session
        target_date: Date to populate with fallback data
    """
    previous_date = target_date - timedelta(days=1)

    existing_today = await db.execute(
        select(CurrencyRate).where(CurrencyRate.rate_date == target_date)
    )
    if existing_today.scalar_one_or_none():
        logger.info(f"Rates already exist for {target_date}, skipping fallback")
        return

    previous_rates = await db.execute(
        select(CurrencyRate).where(CurrencyRate.rate_date == previous_date)
    )
    rates = previous_rates.scalars().all()

    if not rates:
        logger.warning(f"No fallback rates found for {previous_date}")
        return

    for rate in rates:
        new_rate = CurrencyRate(
            currency_code=rate.currency_code,
            rate=rate.rate,
            rate_date=target_date,
        )
        db.add(new_rate)

    await db.flush()
    logger.info(
        f"Fallback: copied rates from {previous_date} to {target_date}"
    )


async def get_rate(
    db: AsyncSession,
    currency_code: str,
    target_date: Optional[date] = None,
) -> Optional[float]:
    """
    Get exchange rate for currency on target date.

    Args:
        db: Database session
        currency_code: Currency code (e.g., 'USD')
        target_date: Date to get rate for (default: today)

    Returns:
        Exchange rate to RUB, or None if not found
    """
    if target_date is None:
        target_date = date.today()

    result = await db.execute(
        select(CurrencyRate).where(
            and_(
                CurrencyRate.currency_code == currency_code,
                CurrencyRate.rate_date == target_date,
            )
        )
    )
    rate_record = result.scalar_one_or_none()
    return rate_record.rate if rate_record else None


async def convert_to_rub(
    db: AsyncSession,
    amount: float,
    from_currency: str,
    op_date: Optional[date] = None,
) -> float:
    """
    Convert amount from foreign currency to RUB.

    Args:
        db: Database session
        amount: Amount to convert
        from_currency: Source currency code (e.g., 'USD')
        op_date: Operation date (default: today)

    Returns:
        Amount converted to RUB
    """
    if from_currency == "RUB":
        return amount

    if op_date is None:
        op_date = date.today()

    rate = await get_rate(db, from_currency, op_date)

    if rate:
        return amount * float(rate)

    # Fallback: use last known rate
    result = await db.execute(
        select(CurrencyRate)
        .where(CurrencyRate.currency_code == from_currency)
        .order_by(CurrencyRate.rate_date.desc())
        .limit(1)
    )
    last_rate_record = result.scalar_one_or_none()

    if last_rate_record:
        logger.warning(
            f"Rate for {from_currency} on {op_date} not found, "
            f"using last known rate from {last_rate_record.rate_date}: {last_rate_record.rate}"
        )
        return amount * float(last_rate_record.rate)

    logger.warning(
        f"No rate found for {from_currency}, returning original amount"
    )
    return amount


async def revalue_currency_accounts(
    db: AsyncSession,
    company_id: UUID,
    op_date: date,
) -> None:
    """
    Revalue foreign currency accounts and generate FX gains/losses journal entries.

    Args:
        db: Database session
        company_id: Company ID for IDOR protection
        op_date: Operation date for revaluation

    Logic:
        1. Find all non-RUB accounts for company
        2. For each account: calculate RUB equivalent on op_date and op_date-1
        3. If difference != 0: create journal entry with FX gain/loss
           - Gain (diff > 0): D 5100 / K 9101
           - Loss (diff < 0): D 9102 / K 5100
    """
    from app.services.ledger_service import post_double_entry

    # Find all non-RUB accounts for this company
    result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company_id,
                Account.currency != "RUB",
                Account.is_deleted.is_(False),
            )
        )
    )
    accounts = result.scalars().all()

    for account in accounts:
        amount = float(account.current_balance)

        # Convert to RUB on current date and previous date
        rub_today = await convert_to_rub(
            db, amount, account.currency, op_date
        )
        rub_yesterday = await convert_to_rub(
            db, amount, account.currency, op_date - timedelta(days=1)
        )

        # Calculate FX difference
        fx_difference = Decimal(str(rub_today - rub_yesterday)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if fx_difference == 0:
            continue

        # Determine if gain or loss
        if fx_difference > 0:
            # FX Gain: D 5100 / K 9101
            postings = [
                {"code": "5100", "debit": fx_difference, "credit": 0},
                {"code": "9101", "debit": 0, "credit": fx_difference},
            ]
        else:
            # FX Loss: D 9102 / K 5100
            postings = [
                {"code": "9102", "debit": abs(fx_difference), "credit": 0},
                {"code": "5100", "debit": 0, "credit": abs(fx_difference)},
            ]

        try:
            await post_double_entry(
                db=db,
                company_id=company_id,
                date_=op_date,
                description=f"FX Revaluation: {account.currency} account {account.name}",
                doc_type="fx_revaluation",
                doc_id=account.id,
                postings=postings,
            )
            logger.info(
                f"FX revaluation: {account.name} ({account.currency}) "
                f"difference: {fx_difference} RUB"
            )
        except Exception as e:
            logger.error(
                f"Failed to post FX revaluation for {account.name}: {e}"
            )
