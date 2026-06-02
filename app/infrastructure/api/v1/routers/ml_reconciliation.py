"""ML-based document reconciliation API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance, CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.ml_reconciliation_service import auto_match_documents, suggest_document_matches

router = APIRouter(prefix="/companies/{company_id}/reconciliation", tags=["ML Reconciliation"])


@router.post("/auto-match", status_code=200)
async def auto_match(
    company_id: UUID,
    threshold: float = Query(0.85, ge=0.5, le=1.0),
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Auto-match documents to transactions using ML scoring."""
    return await auto_match_documents(db, company_id, threshold)


@router.get("/suggestions/{document_id}", status_code=200)
async def get_suggestions(
    company_id: UUID,
    document_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get top 5 transaction matches for a document."""
    return await suggest_document_matches(db, company_id, document_id)
