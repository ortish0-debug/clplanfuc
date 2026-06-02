"""ML-based document reconciliation and automatic matching."""
from uuid import UUID
from datetime import datetime, timedelta

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.documents import Document, DocumentTransactionLink
from app.domain.models.finance import Transaction


def calculate_similarity_score(doc_amount: float, trans_amount: float, date_diff: int) -> float:
    """
    Calculate similarity score between document and transaction.
    Factors: amount match, date proximity, counterparty.
    """
    # Amount similarity (0-1)
    amount_diff = abs(doc_amount - trans_amount)
    amount_similarity = 1.0 - min(1.0, amount_diff / max(doc_amount, trans_amount, 1))

    # Date proximity (0-1): closer = higher score
    date_similarity = max(0, 1.0 - (date_diff / 30))  # 30 days window

    # Combined score (weighted)
    score = (amount_similarity * 0.7) + (date_similarity * 0.3)
    return score


async def auto_match_documents(db: AsyncSession, company_id: UUID, threshold: float = 0.85) -> dict:
    """
    Automatically match documents to transactions using ML scoring.
    Matches documents with transactions based on amount, date, counterparty.
    """
    # Get unmatched documents
    unmatched_docs = await db.execute(
        select(Document).where(
            Document.company_id == company_id,
            Document.status.in_(['draft', 'pending']),
        )
    )
    documents = unmatched_docs.scalars().all()

    matched_count = 0
    matches = []

    for doc in documents:
        # Get transactions in ±15 days window
        date_range_start = doc.created_at - timedelta(days=15)
        date_range_end = doc.created_at + timedelta(days=15)

        trans_result = await db.execute(
            select(Transaction).where(
                and_(
                    Transaction.company_id == company_id,
                    Transaction.created_at >= date_range_start,
                    Transaction.created_at <= date_range_end,
                    Transaction.counterparty_id == doc.counterparty_id,
                )
            )
        )
        transactions = trans_result.scalars().all()

        # Find best match
        best_match = None
        best_score = 0

        for trans in transactions:
            date_diff = abs((trans.created_at - doc.created_at).days)
            score = calculate_similarity_score(
                float(doc.total_amount),
                float(trans.amount),
                date_diff,
            )

            if score > best_score:
                best_score = score
                best_match = trans

        # Auto-link if score above threshold
        if best_match and best_score >= threshold:
            link = DocumentTransactionLink(
                document_id=doc.id,
                transaction_id=best_match.id,
                linked_amount=doc.total_amount,
            )
            db.add(link)
            doc.status = 'paid'

            matches.append({
                "document_id": str(doc.id),
                "transaction_id": str(best_match.id),
                "score": round(best_score, 3),
                "amount": float(doc.total_amount),
            })
            matched_count += 1

    if matched_count > 0:
        await db.commit()

    return {
        "status": "success",
        "auto_matched": matched_count,
        "threshold_used": threshold,
        "matches": matches,
    }


async def suggest_document_matches(db: AsyncSession, company_id: UUID, document_id: UUID) -> dict:
    """
    Suggest top 5 matching transactions for a document.
    Returns candidates ranked by similarity score.
    """
    doc_result = await db.execute(
        select(Document).where(Document.id == document_id, Document.company_id == company_id)
    )
    doc = doc_result.scalar_one_or_none()

    if not doc:
        return {"status": "error", "message": "Document not found"}

    # Get transactions ±30 days
    date_range_start = doc.created_at - timedelta(days=30)
    date_range_end = doc.created_at + timedelta(days=30)

    trans_result = await db.execute(
        select(Transaction).where(
            and_(
                Transaction.company_id == company_id,
                Transaction.created_at >= date_range_start,
                Transaction.created_at <= date_range_end,
            )
        )
    )
    transactions = trans_result.scalars().all()

    # Score all transactions
    candidates = []
    for trans in transactions:
        date_diff = abs((trans.created_at - doc.created_at).days)
        score = calculate_similarity_score(
            float(doc.total_amount),
            float(trans.amount),
            date_diff,
        )
        candidates.append({
            "transaction_id": str(trans.id),
            "amount": float(trans.amount),
            "date": str(trans.created_at.date()),
            "similarity_score": round(score, 3),
        })

    # Sort by score and return top 5
    candidates.sort(key=lambda x: x["similarity_score"], reverse=True)

    return {
        "status": "success",
        "document_id": str(document_id),
        "doc_amount": float(doc.total_amount),
        "candidates": candidates[:5],
    }
