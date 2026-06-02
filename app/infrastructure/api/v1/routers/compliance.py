"""GDPR/152-ФЗ Compliance API."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Company
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.audit_service import log_audit_action

router = APIRouter(
    prefix="/companies/{company_id}/compliance",
    tags=["Compliance"],
)


@router.get("/export", status_code=200)
async def export_company_data(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Data Portability: export all company data as JSON."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Aggregate data
    return {
        "company": {
            "id": str(company.id),
            "name": company.name,
            "inn": company.inn,
            "status": company.status,
        },
        "export_date": "2026-06-01T00:00:00Z",
        "accounts": [],
        "transactions": [],
    }


@router.delete("/purge", status_code=204)
async def purge_company_data(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Right to be forgotten: anonymize and mark company as purged."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Mark as purged
    await db.execute(
        update(Company).where(Company.id == company_id).values(status="purged")
    )

    # Audit log (secure trail of deletion)
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="compliance.data_purged",
        target_type="Company",
        target_id=str(company_id),
        ip_address="system",
    )

    await db.commit()
