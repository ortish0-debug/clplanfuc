"""1C:Enterprise integration API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.onec_service import sync_onec_payload

router = APIRouter(prefix="/companies/{company_id}/onec", tags=["1C Integration"])


class OneCPayloadItem(BaseModel):
    onec_guid: str
    entity_type: str
    name: str = None
    inn: str = None
    account_id: UUID = None
    counterparty_id: UUID = None
    amount: float = None
    description: str = None


class OneCPayload(BaseModel):
    items: list[OneCPayloadItem]


@router.post("/sync", status_code=200)
async def sync_onec(
    company_id: UUID,
    payload: OneCPayload,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sync 1C data (transactions, counterparties)."""
    result = await sync_onec_payload(
        db,
        company_id,
        [item.model_dump() for item in payload.items],
    )
    await db.commit()
    return result
