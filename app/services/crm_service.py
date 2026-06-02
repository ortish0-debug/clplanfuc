"""CRM reconciliation."""
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.models.documents import Document, DocumentTransactionLink

async def generate_reconciliation_act(db: AsyncSession, company_id: UUID, counterparty_id: UUID) -> dict:
    """Generate reconciliation act for counterparty."""
    doc_result = await db.execute(
        select(func.sum(Document.total_amount)).where(
            Document.company_id == company_id,
            Document.counterparty_id == counterparty_id
        )
    )
    total_docs = doc_result.scalar() or 0
    
    link_result = await db.execute(
        select(func.sum(DocumentTransactionLink.linked_amount)).where(
            Document.id == DocumentTransactionLink.document_id,
            Document.counterparty_id == counterparty_id
        )
    )
    total_paid = link_result.scalar() or 0
    balance = float(total_docs) - float(total_paid)
    
    return {
        "counterparty_id": str(counterparty_id),
        "balance": balance,
        "status": "reconciled"
    }
