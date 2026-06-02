"""Document linking and reconciliation."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.documents import Document, DocumentTransactionLink


async def link_document_to_transaction(db: AsyncSession, document_id: UUID, transaction_id: UUID, amount: float) -> bool:
    """Link document to transaction and auto-update status if paid."""
    try:
        link = DocumentTransactionLink(
            document_id=document_id,
            transaction_id=transaction_id,
            linked_amount=Decimal(str(amount))
        )
        db.add(link)
        await db.flush()

        result = await db.execute(
            select(func.sum(DocumentTransactionLink.linked_amount)).where(
                DocumentTransactionLink.document_id == document_id
            )
        )
        total_linked = result.scalar() or Decimal("0.00")

        doc_result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc and total_linked >= doc.total_amount:
            await db.execute(
                update(Document).where(Document.id == document_id).values(status="paid")
            )

        await db.flush()
        return True
    except Exception:
        return False
