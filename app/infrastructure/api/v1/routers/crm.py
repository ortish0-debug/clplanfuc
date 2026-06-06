"""CRM API."""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.crm_service import generate_reconciliation_act

router = APIRouter(prefix="/companies/{company_id}/crm", tags=["CRM"])

@router.get("/{counterparty_id}/reconcile", status_code=200)
async def reconcile_counterparty(
    company_id: UUID,
    counterparty_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate reconciliation act."""
    return await generate_reconciliation_act(db, company_id, counterparty_id)
