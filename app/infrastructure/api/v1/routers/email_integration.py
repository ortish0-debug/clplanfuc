"""Email integration API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.email_service import poll_email_and_ocr_invoices

router = APIRouter(prefix="/companies/{company_id}/email", tags=["Email Integration"])


@router.post("/fetch-trigger", status_code=200)
async def fetch_and_ocr_emails(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Fetch emails and OCR invoices, create Document records."""
    result = await poll_email_and_ocr_invoices(db, company_id)
    await db.commit()
    return result
