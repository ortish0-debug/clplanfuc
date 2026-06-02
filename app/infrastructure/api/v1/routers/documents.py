"""Documents API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.documents import Document
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.document_service import link_document_to_transaction
from app.services.pdf_generator_service import generate_invoice_pdf

router = APIRouter(
    prefix="/companies/{company_id}/documents",
    tags=["Documents"],
)


class CreateDocumentRequest(BaseModel):
    doc_type: str
    doc_number: str
    total_amount: float
    currency: str = "RUB"
    counterparty_id: UUID = None


class LinkDocumentRequest(BaseModel):
    transaction_id: UUID
    amount: float


@router.post("", status_code=201)
async def create_document(
    company_id: UUID,
    request: CreateDocumentRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create document."""
    doc = Document(
        company_id=company_id,
        doc_type=request.doc_type,
        doc_number=request.doc_number,
        total_amount=request.total_amount,
        currency=request.currency,
        counterparty_id=request.counterparty_id,
    )
    db.add(doc)
    await db.commit()
    return {"status": "success", "document_id": str(doc.id)}


@router.post("/{document_id}/link", status_code=200)
async def link_document(
    company_id: UUID,
    document_id: UUID,
    request: LinkDocumentRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Link document to transaction."""
    success = await link_document_to_transaction(
        db, document_id, request.transaction_id, request.amount
    )
    if success:
        await db.commit()
        return {"status": "success"}
    return {"status": "error"}


@router.get("", status_code=200)
async def list_documents(
    company_id: UUID,
    doc_type: str = Query(None),
    status: str = Query(None),
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> list:
    """List documents with filtering."""
    query = select(Document).where(Document.company_id == company_id)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if status:
        query = query.where(Document.status == status)
    result = await db.execute(query)
    docs = result.scalars().all()
    return [{"id": str(d.id), "doc_type": d.doc_type, "total_amount": float(d.total_amount), "status": d.status} for d in docs]


@router.get("/{document_id}/generate-pdf", status_code=200)
async def generate_pdf(
    company_id: UUID,
    document_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Generate PDF for document."""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.company_id == company_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return {"status": "error", "message": "Document not found"}

    lines = [{"name": "Услуга", "quantity": 1, "price": float(doc.total_amount), "amount": float(doc.total_amount)}]
    pdf_bytes = generate_invoice_pdf(
        company_name="ПланФакт",
        invoice_number=doc.doc_number,
        invoice_date=str(doc.created_at.date()) if doc.created_at else "2026-06-01",
        lines=lines,
        total_amount=float(doc.total_amount),
        counterparty="Контрагент",
    )

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{doc.doc_number}.pdf"},
    )
