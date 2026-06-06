"""1C:Enterprise integration service."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.counterparties import Counterparty
from app.domain.models.finance import Transaction
from app.domain.models.onec_mapping import OneCMapping


async def sync_onec_payload(db: AsyncSession, company_id: UUID, payload: list[dict]) -> dict:
    """
    Sync 1C payload (transactions and counterparties).

    For each item in payload:
    - Check if onec_guid exists in OneCMapping
    - If found: update entity, else: create new entity + mapping
    """
    synced_count = 0

    for item in payload:
        onec_guid = item.get('onec_guid', '')
        entity_type = item.get('entity_type', '')

        if not onec_guid or not entity_type:
            continue

        mapping_result = await db.execute(
            select(OneCMapping).where(
                OneCMapping.onec_guid == onec_guid,
                OneCMapping.company_id == company_id,
            )
        )
        mapping = mapping_result.scalar_one_or_none()

        if mapping:
            mapping.updated_at = __import__('datetime').datetime.utcnow()
            synced_count += 1
        else:
            if entity_type == 'counterparty':
                counterparty = Counterparty(
                    company_id=company_id,
                    name=item.get('name', ''),
                    inn=item.get('inn', ''),
                )
                db.add(counterparty)
                await db.flush()
                mapping = OneCMapping(
                    company_id=company_id,
                    entity_type='counterparty',
                    internal_id=counterparty.id,
                    onec_guid=onec_guid,
                )
                db.add(mapping)
                synced_count += 1

            elif entity_type == 'transaction':
                transaction = Transaction(
                    company_id=company_id,
                    account_id=item.get('account_id'),
                    counterparty_id=item.get('counterparty_id'),
                    amount=item.get('amount', 0),
                    description=item.get('description', ''),
                )
                db.add(transaction)
                await db.flush()
                mapping = OneCMapping(
                    company_id=company_id,
                    entity_type='transaction',
                    internal_id=transaction.id,
                    onec_guid=onec_guid,
                )
                db.add(mapping)
                synced_count += 1

    return {"synced_count": synced_count, "status": "success"}
